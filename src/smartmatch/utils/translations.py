"""
====================================================================
SMART MATCH AFRICA - TRILINGUAL TRANSLATION & LOCALIZATION ENGINE
====================================================================
Supported Languages:
- English ('en')
- Amharic ('am')
- Afan Oromo ('om')
"""

import logging
import re
from typing import List, Optional

logger = logging.getLogger("smartmatch.translations")


# ==========================================
# 1. TELEGRAM MARKDOWN SANITIZER HELPER
# ==========================================
def sanitize_telegram_markdown(text: str) -> str:
    """
    Globally converts HTML formatting tags into Telegram-compliant Markdown format.
    Ensures safe output when sending messages with parse_mode="Markdown".
    """
    if not text:
        return ""

    # Convert HTML bold tags to Markdown bold (*)
    text = re.sub(r'</?(?:b|strong)>', '*', text, flags=re.IGNORECASE)

    # Convert HTML italic tags to Markdown italic (_)
    text = re.sub(r'</?(?:i|em)>', '_', text, flags=re.IGNORECASE)

    # Convert HTML code tags to Markdown inline code (`)
    text = re.sub(r'</?code>', '`', text, flags=re.IGNORECASE)

    # Convert HTML pre tags to Markdown block code (```)
    text = re.sub(r'</?pre>', '```', text, flags=re.IGNORECASE)

    return text


# ==========================================
# 2. MASTER TRANSLATION DICTIONARY
# ==========================================
TRANSLATIONS = {
    "en": {
        # =================================
        # --- SYSTEM & ERRORS (sys_) ---
        # =================================
        "sys_error_generic": "⚠️ We couldn't process that. Please try again or contact support.",
        "sys_invalid_number": "⚠️ Invalid input. Please enter numbers only (e.g., 1500):",
        "sys_invalid_text": "⚠️ Invalid input. Please enter valid text.",
        "sys_cancelled": "🚫 Action cancelled. Returning to main menu.",
        "sys_loading": "⏳ Processing your request...",
        "sys_photo_prompt": "📸 Please upload a clear photo, or click 'Skip'.",

        # ==================================
        # --- COMMON BUTTONS (btn_) ---
        # ==================================
        "btn_yes": "Yes",
        "btn_no": "No",
        "btn_cancel": "❌ Cancel",
        "btn_confirm": "✅ Confirm",
        "btn_back": "⬅️ Back",

        # =====================================================================
        # MAIN MENU & WELCOME MESSAGE
        # =====================================================================
        "welcome_back": (
            "⚡ *WELCOME TO SMART MATCH AFRICA!* ⚡\n\n"
            "> *Your Real-Time Asset & Mobility Marketplace*\n\n"
            "Our permanent menu is locked at the bottom of your screen.\n\n"
            "*Quick Capabilities:*\n"
            "• 🏠 *Housing:* List or find residential & commercial properties\n"
            "• 🚘 *Vehicles:* Offer or request personal & commercial vehicles\n"
            "• 🚜 *Equipment:* Heavy machinery & agricultural equipment\n"
            "• 📦 *Logistics:* Freight transport & city ride services\n\n"
            "👇 *Select an option from the keyboard below to start:*"
        ),
        "menu_unrecognized": (
            "⚠️ *Unrecognized Command*\n\n"
            "Please select an official option from the persistent keyboard menu below."
        ),
        # ----------------------------------------------------
        # MAIN MENU BUTTON LABELS (2x4 Grid Matrix)
        # ----------------------------------------------------
        "btn_list_house": "🏠 LIST HOUSE",
        "btn_find_house": "🔍 FIND HOUSE",
        "btn_list_vehicle": "🚘 LIST VEHICLE",
        "btn_find_vehicle": "🔎 FIND VEHICLE",
        "btn_list_equipment": "🚜 LIST EQUIPMENT",
        "btn_find_equipment": "🏗️ FIND EQUIPMENT",
        "btn_freight": "📦 FREIGHT",
        "btn_ride": "🚗 RIDE",

        # ----------------------------------------------------
        # MENU ENTRY PROMPTS (Matching equipment_rental style)
        # ----------------------------------------------------
        "menu_freight": "🚛 **Freight Logistics**\n\nSelect whether you want to ship goods or offer carrier capacity:",
        "menu_ride": "🚕 **Ride-Sharing**\n\nSelect whether you are a passenger needing a ride or a driver offering "
                     "one:",
        "menu_list_house": "🏠 **List a Property**\n\nSelect the transaction type for the real estate asset you are "
                           "listing:",
        "menu_find_house": "🔍 **Source a Property**\n\nSelect the transaction type for the real estate asset you "
                           "wish to procure:",
        "menu_list_vehicle": "🚗 **List a Vehicle**\n\nSelect the transaction type for the vehicle you are listing:",
        "menu_find_vehicle": "🔍 **Source a Vehicle**\n\nSelect the transaction type for the vehicle you need to "
                             "procure:",

        "msg_choose_language_prompt": "🌐 *Welcome to Smart Match Africa!*\n\nPlease select your preferred "
                                      "language:\nእባክዎ የሚፈልጉትን ቋንቋ ይምረጡ:\nMaaloo afaan keessan filadhaa:",
        "msg_choose_language_short": "🌐 Choose Language / ቋንቋ ይምረጡ / Afaan Filadhu:",
        "msg_lang_set_success": "✅ Language set to English successfully.",

        # ==========================================================
        # --- EQUIPMENT RENTAL (equip_) ---
        # ==========================================================
        # --- 1. MAIN MENU & CATEGORIES ---
        "menu_list_equip": "🏗️ **List Equipment for Rent**\n\nSelect the primary classification for your machinery "
                           "or infrastructure asset:",
        "menu_find_equip": "🔍 **Source Equipment**\n\nSelect the operational category of the equipment you need to "
                           "procure:",

        "btn_cat_construction": "🚜 Construction & Earthmoving",
        "btn_cat_farming": "🌾 Agriculture & Irrigation",
        "btn_cat_generators": "⚡ Generators & Power",
        "btn_cat_eventtech": "🎪 Event Tech & Commercial",
        # --- 2. DATA INTAKE PIPELINE (LISTING & SOURCING) ---
        "prompt_equip_profile": "📝 **Asset Profiling: {category}**\n\nPlease enter the specific equipment type ("
                                "e.g., Hydraulic Excavator, Dewatering Pump, Scaffolding):",
        "prompt_sourcing_req": "📋 **Procurement Requirement: {category}**\n\nWhat specific equipment/machinery are "
                               "you looking to rent?",
        "err_name_too_short": "⚠️ The equipment designation is too short. Please provide a clear, professional "
                              "description.",

        "prompt_capacity_list": "⚙️ **Capacity & Output**\n\nWhat is the capacity, rating, or output specification "
                                "for the *{equip_type}*? (e.g., 20 Tons, 500 kVA, 150 L/s):",
        "prompt_capacity_find": "⚙️ **Required Specifications**\n\nWhat minimum capacity or output rating is required "
                                "for the *{equip_type}*? (Reply with 'Any' if flexible):",

        "prompt_brand_list": "🏷️ **Make & Technical Features**\n\nEnter the Manufacturer/Brand and any key technical "
                             "features, separated by a hyphen (e.g., Caterpillar - GPS Guided, or KSB - Submersible):",
        "prompt_brand_find": "🏷️ **Preferred Make**\n\nDo you have a preferred manufacturer or specific feature "
                             "requirement? (e.g., Caterpillar - GPS Guided Reply with 'None' if flexible):",

        "prompt_location_list": "📍 **Asset Location**\n\nPlease share the current location/site of the equipment "
                                "using Telegram's location attachment (📎), or type the site address:",
        "prompt_location_find": "📍 **Deployment Site**\n\nWhere will this equipment be deployed? Share the project "
                                "site location (📎) or type the address:",
        "gps_coords_provided": "🌐 GPS Coordinates Logged",

        # --- 3. COMMERCIALS & CONDITION ---
        "prompt_rate_list": "💰 **Hourly Rate (ETB/USD)**\n\nEnter your asking hourly rental rate by clearly "
                            "specifying the currency as ETB/USD:",
        "prompt_rate_find": "💰 **Budget Ceiling (ETB/USD)**\n\nEnter your maximum acceptable hourly rate by clearly "
                            "specifying the currency as ETB/USD:",
        "err_invalid_rate": "⚠️ Invalid input. Please enter a numerical value for the rate.",
        "err_invalid_rate_system": "❌ System encountered an error validating the commercial rate. Please try again.",

        "prompt_condition_list": "🛠️ **Asset Condition**\n\nSelect the current mechanical condition of the equipment:",
        "prompt_condition_find": "🛠️ **Minimum Acceptable Condition**\n\nSelect the minimum mechanical condition "
                                 "required for your project:",
        "btn_cond_exc": "🌟 Excellent / Like New",
        "btn_cond_good": "✅ Good / Field Ready",
        "btn_cond_fair": "⚠️ Fair / Operational",

        # --- 4. MEDIA & CONTACT ---
        "prompt_photo_upload": "📸 **Asset Documentation**\n\nPlease upload a clear, current photo of the equipment "
                               "at the site (📎). Type 'Skip' to proceed without a photo.",
        "msg_compressing_image": "⏳ Compressing and uploading asset imagery to secure cloud storage...",
        "err_upload_timeout": "⚠️ Image upload timed out. Proceeding with text profile only.",

        "prompt_contact_info": "📞 **Direct Contact Number**\n\nPlease provide the phone number for the site manager "
                               "or equipment dispatcher:",
        "msg_finalizing_packet": "⚙️ Compiling and synchronizing data packet...",
        "msg_asset_live": "✅ **Listing Activated!**\n\nYour {category} asset (*{equip_type}*) is now live on the "
                          "Smart Match grid and available for contractors.",

        # --- 5. MATCHING & DEALS ---
        "msg_scanning_nodes": "📡 Scanning localized nodes for available matching your parameters...",
        "msg_request_broadcasted": "📢 **Sourcing Request Broadcasted**\n\nNo immediate match found. Your request has "
                                   "been queued and broadcasted to regional resource providers. You will be notified "
                                   "when an asset becomes available.",
        "MSG_TOP_EQUIP_MATCHES": "🎯 **Top {count} Equipment Matches Found!**\n\nPlease select an asset below to "
                                 "review and send an offer:",

        "msg_machinery_secured": "🎯 **Equipment Match Found! (Match Score: {score}%)**\n\n**Asset:** {brand} {"
                                 "equip_type}\n**Location:** {location}\n**Rate:** {rate} ETB\n\nWould you like to "
                                 "secure this Equipment and begin the commission process?",
        "btn_rent_secure": "🔒 Secure Asset & Pay Escrow",
        "btn_decline": "❌ Decline",
        "err_system_anomaly": "❌ An internal pipeline anomaly occurred. Please restart the process.",
        "err_request_not_found": "⚠️ Selected request is no longer active.",

        # --- 6. BILLING & ESCROW ---
        "msg_initiating_gateway": "💳 Initiating secure payment gateway...",
        "msg_asset_link_secured": "🧾 **Invoice Generated**\n\nPlease complete the escrow payment to lock the "
                                  "equipment schedule and receive deployment contact details.",
        "btn_open_portal": "🌐 Open Payment Portal",
        "btn_verify_payment": "🔄 Verify Payment Status",
        "err_gateway_request": "❌ Could not reach the billing gateway. Please try again later.",
        "msg_escrow_cleared": "✅ **Escrow Cleared & Asset Secured!**\n\nThe transaction is complete. \n\n**Provider "
                              "Contact:** {provider_contact}\n\nPlease coordinate directly for site mobilization.",
        "msg_payment_pending": "⏳ Payment is still pending. Please complete the transaction on the portal and click "
                               "verify again.",

        # --- 7. NEGOTIATION / OFFERS ---
        "equip_offer_prompt": "🤝 **Initiate Offer**\n\nPlease enter your initial proposed rate (in ETB) for this "
                              "equipment:",
        "btn_cancel_offer": "🚫 Cancel Offer",
        "err_listing_unavailable": "❌ This machinery listing is no longer active or available.",
        "err_offer_own_listing": "⚠️ You cannot initiate a commercial offer on your own equipment.",
        "err_offer_failed": "❌ Failed to initiate the negotiation workflow. Please try again.",
        "msg_process_cancelled": "🛑 Equipment procedure terminated.",

        # Match Selection & Asset Card
        "msg_no_matches_found": "⚠️ No active matches found at the moment. Returning to main menu.",
        "err_asset_not_found": "⚠️ Selected equipment is no longer active or available.",
        "lbl_equipment_selected": "🚜 <b>Equipment Selected:</b> {asset_title}",
        "lbl_rental_rate": "💰 <b>Rental Rate:</b> <code>{price:,.2f} ETB</code>",
        "lbl_capacity_output": "⚡ <b>Capacity/Output:</b> <code>{capacity}</code>",
        "prompt_select_option": "To submit a proposal price, select one of the following options:",

        # Custom Offer Prompt & Processing
        "prompt_custom_offer": (
            "💬 <b>Enter Your Proposed Offer</b>\n\n"
            "🚜 Equipment: <i>{model_name}</i>\n"
            "📍 Location: <b>{location_text}</b>\n"
            "💰 Asking Rate: <code>{asking_price:,.2f} ETB</code>\n\n"
            "Please reply with your offer amount in ETB (numbers only, e.g., <code>5000</code>):"
        ),

        # Transmission & Deal Locking Status
        "msg_offer_transmitted": (
            "✅ <b>Offer Transmitted!</b>\n\n"
            "Your proposed rate of <b>{formatted_price} ETB</b> for <i>{safe_asset_title}</i> "
            "has been securely forwarded to the owner."
        ),
        "msg_equipment_selected": "🚜 <b>Equipment Selected:</b>",
        "msg_property_use": "🛠️ <b>Equipment Type:</b>",
        "msg_rental_rate": "💰 <b>Rental Rate:</b>",
        "msg_capacity_output": "⚡ <b>Capacity/Output:</b>",
        "msg_select_offer_option_equip": "Select an option below to submit your offer to the owner:",
        "msg_location": "📍 <b>Location:</b>",
        "err_self_offer": "⚠️ You cannot submit an offer on your own listing.",
        "msg_select_top_match": "🚜 <b>Top Matching Equipment Options:</b>\n\nPlease select one of the options below:",
        "msg_select_transport_match": "🚚 <b>Top Matching Transport Options:</b>\n\nPlease select one of the options "
                                      "below:",
        "msg_select_housing_match": "🏠 <b>Top Matching Housing Options:</b>\n\nPlease select one of the options "
                                    "below:",
        "BTN_CANCEL": "❌ Cancel Search",
        "msg_scanning_seekers": "Scanning for matching seekers...",
        "BTN_SKIP": "Skip",
        "msg_top_seekers_found": "Top matching seekers found!",
        "msg_seeker_request_selected_equip": "Seeker request selected successfully. Processing match...",
        "msg_no_seeker_matches": "No matching requests found at this time. We will notify you as soon as one is "
                                 "received.",
        "dispatch_reverse_seeker_body": (
            "Commission settled successfully for your request: <b>{asset_title}</b> "
            "(Deal ID: <code>{deal_id}</code>).\nAgreed Price: <b>{price} {currency}</b>"
        ),
        "dispatch_reverse_provider_body": (
            "An asset seeker has settled the commission for your matched request: <b>{asset_title}</b> "
            "(Deal ID: <code>{deal_id}</code>).\nAgreed Price: <b>{price} {currency}</b>"
        ),
        "dispatch_reverse_match_seeker_header": "✅ <b>Match Finalized! (Reverse Match)</b>",
        "dispatch_reverse_match_provider_header": "✅ <b>Match Finalized! (Reverse Match)</b>",

        # ==================================================
        # --- HOUSING (house_) ---
        # ==================================================
        # Navigation & System
        "msg_return_hub": "Returning to the main menu...",
        "msg_return_root": "We are awaiting your response. Please be patient, use the main menu, or send /start to "
                           "switch to another task.",
        "btn_return_main": "🔙 Main Menu",

        # Application Entry Prompts
        "msg_list_house_prompt": "🏡 **List a Property**\n\nAre you looking to rent out or sell your property?",
        "btn_rent_out": "🔑 Rent Out",
        "btn_sell": "💰 Sell",
        "msg_find_house_prompt": "🔍 **Find a Property**\n\nAre you looking to rent or buy a property?",
        "btn_rent_in": "🔑 Rent",
        "btn_buy": "💰 Buy",

        # Sector Classification
        "msg_property_sector": "🏢 **Property Sector**\n\nIs this a residential or commercial property?",
        "btn_residential": "🏠 Residential House ",
        "btn_commercial": "🏢 Commercial Place",
        "btn_residential_plot": "Residential Plot",
        "btn_commercial_plot": "Commercial Plot",

        # Location
        "msg_location_list": "📍 **Location**\n\nPlease share the property's location using the Telegram attachment ("
                             "📎) or type the City, Sub-city/District (or Woreda)/Kebele, and neighborhood.",
        "msg_location_find": "📍 **Target Location**\n\nWhere are you looking? Please share a location pin or type "
                             "the City, Sub-city/District (or Woreda)/Kebele, and neighborhood.",
        "err_location_short": "⚠️ Location description is too short. Please provide a more specific area or "
                              "neighborhood.",

        # Financial Parameters
        "msg_price_list": "💵 **Price / Rate**\n\nPlease enter your asking price and specify the currency (e.g., 25,"
                          "000 ETB or 500 USD for monthly rent, or 1,500,000 ETB or 30,000 USD for sale).",
        "msg_budget_find": "💵 **Maximum Budget**\n\nPlease enter your maximum budget and specify the currency (e.g., "
                           "25,000 ETB or 500 USD for monthly rent, or 1,500,000 ETB or 20,500 USD for sale).",
        "err_invalid_number": "⚠️ Invalid amount. Please enter numbers only, without commas or text (e.g., 25000).",

        # Size & Specifications
        "msg_beds_list": "🛏️ **Bedrooms**\n\nHow many bedrooms does the property have? Enter a whole number (e.g., "
                         "3).",
        "msg_beds_find": "🛏️ **Bedrooms**\n\nWhat is the minimum number of bedrooms you need? Enter a whole number ("
                         "e.g., 2).",
        "msg_sqm_list": "📏 **Size (Sq Meters)**\n\nWhat is the size of the space in square meters? Enter a whole "
                        "number.",
        "msg_sqm_find": "📏 **Size (Sq Meters)**\n\nWhat is the minimum size you need in square meters? Enter a whole"
                        " number.",
        "err_invalid_whole_number": "⚠️ Invalid entry. Please enter a valid whole number (e.g., 3).",

        # Amenities
        "keyword_none": "none",
        "msg_amenities_list": "✨ **Amenities**\n\nList the key amenities separated by commas (e.g., Parking, WiFi, "
                              "Generator, Backup Water). Type 'None' if there are no specific amenities.",
        "msg_amenities_find": "✨ **Required Amenities**\n\nList your must-have amenities separated by commas (e.g., "
                              "Parking, Security, Elevator). Type 'None' if you have no strict requirements.",

        # Photo Upload Pipeline
        "keyword_skip": "skip",
        "msg_photo_prompt": "📸 **Property Photo**\n\nPlease upload a clear photo of the property. If you want to "
                            "skip this step, type 'Skip'.",
        "msg_uploading_b2": "⏳ Uploading photo to secure storage...",
        "msg_photo_success": "✅ Photo uploaded successfully!",
        "msg_photo_timeout": "⚠️ Upload failed due to a timeout. We will proceed without a photo.",
        "err_photo_invalid": "⚠️ Invalid input. Please upload a photo or type 'Skip' to proceed without one.",
        "msg_bulk_qty": "📦 Please enter the total bulk quantity available:",

        # Contact Information
        "msg_contact_list": "📞 **Contact Information**\n\nPlease enter your phone number so verified clients can "
                            "reach you (e.g., 0911234567).",
        "msg_contact_find": "📞 **Contact Information**\n\nPlease enter your phone number so verified providers can "
                            "reach you when a match is found (e.g., 0911234567).",
        "err_invalid_phone": "⚠️ Invalid phone number. Please enter a valid 9 or 10-digit number.",

        # Database & Match Engine
        "msg_processing_asset": "⚙️ Processing your request and synchronizing with the matching engine...",
        "msg_listing_success": "🎉 **Listing Live!**\n\nYour property has been successfully listed and is now being "
                               "matched with potential clients.",
        "msg_request_success": "📡 **Request Active!**\n\nYour requirements have been saved. We are scanning the "
                               "network and will notify you of top matches.",
        "err_system_db": "❌ An internal database error occurred while saving your request. Please try again later.",
        "lbl_area_size": "Area Size",
        "lbl_bedrooms": "Number of bedrooms: ",
        "lbl_property_selected": "Property Selected",
        "lbl_asking_price": "Asking Price",
        "lbl_select_option": "Select an option below to submit your offer to the landlord:",
        "BTN_ACCEPT_ASKING": "Accept Asking Price",
        "housing_offer_title": "🔔 New HOUSING Offer!",
        "objective": "Objective",
        "asset": "Asset",
        "rental": "Rental",
        "sale": "Sale",
        "residential": "Residential",
        "commercial": "Commercial",
        "offer_price": "Offer",
        "match_btn_format": "🏠 #{idx}: {prop_type} in {location} ({price} ETB - {score}%)",

        # Housing & Match Displays
        "msg_scanning_housing_nodes": "🔍 Scanning available properties...",
        "MSG_TOP_HOUSING_MATCHES": "🎯 <b>Top {count} Matching Properties Found!</b>\n\nPlease select a property "
                                   "below to review and send an offer:",
        "notice_new_offer_received": "🔔 <b>New {vertical_type} Offer!</b>\n\n🏠 <b>Asset:</b> {asset_title}\n💰 "
                                     "<b>Offer:</b> <code>{price} ETB</code>",
        # Buttons & Options
        "BTN_SEND_OFFER": "🤝 Enter Custom Offer",
        "btn_accept_asking": "✅ Accept Asking/Proposed Price ({price} ETB)",

        "lbl_objective": "Objective:",
        "btn_accept_price": "✅ Accept Price",

        # Prompts & Status Messages
        "msg_seeker_request_selected_housing": "👤 <b>Seeker Request Selected:</b>",
        "prompt_enter_offer": (
            "💬 <b>Enter Your Proposed Offer</b>\n\n"
            "Property: <i>{asset_title}</i>\n"
            "Asking Price: <code>{price} ETB</code>\n\n"
            "Please reply with your offer amount in ETB (numbers only, e.g., <code>22000</code>):"
        ),
        "msg_transmitting_offer": "🔄 Transmitting offer...",
        "msg_match_cancelled": "❌ Property search cancelled. Returning to main menu.",

        "alert_search_cancelled": "Search cancelled.",
        "err_verify_provider": "⚠️ Could not verify the property provider. Please try searching again.",
        "msg_offer_sent": "✅ <b>Offer Transmitted Successfully!</b>\n\nYour {transaction_type} offer of <b>{price} "
                          "ETB</b> for <b>{asset_title}</b> has been sent to the provider. You will be notified once "
                          "they respond.",
        "err_deal_init_failed": "❌ Failed to initiate housing negotiation. Please try again later.",

        # Errors, Blockers & Warnings
        "err_internal_pipeline": "⚠️ An internal error occurred while processing your listing. Please try again later.",

        "err_property_not_found": "⚠️ Selected property is no longer active.",
        "err_pending_negotiation": (
            "🚫 <b>Negotiation Blocked</b>\n\n"
            "This property is currently under active negotiation with another user. Please try again later."
        ),
        "err_deal_blocked_conflict": "⚠️ **Negotiation Blocked**\n\nYour session expired or another user is "
                                     "interacting. Please refresh.",
        "err_deal_expired": "⚠️ This deal has expired or is no longer active.",
        "err_target_unavailable": "⚠️ Could not deliver offer. Target user is unavailable.",
        "err_unexpected": "⚠️ An unexpected error occurred.",
        "notice_counter_received": "🔄 **Negotiation Update**\n\n📂 **Type:** {vertical_type}\n🏠 **Asset:** {"
                                   "asset_title}\n👤 **From:** {source_actor}\n💰 **Counter:** **{price} ETB**",
        "role_provider": "Provider",
        "role_seeker": "Seeker",
        "lbl_default_asset": "Asset Listing",

        # Chapa Escrow Gateway
        "btn_pay_chapa": "💳 Pay Commission via Chapa",
        "msg_escrow_commission_fee": "🔐 **Contact Lock**\n\nTo reveal the verified landlord's direct contact "
                                     "information and exact GPS coordinates, please settle the platform commission "
                                     "fee of **{amount} ETB**.",
        "err_chapa_init": "❌ Failed to initialize the Chapa payment gateway. Please try again.",
        "err_chapa_system": "❌ A critical error occurred while connecting to the billing service.",

        # ========================================================
        # --- VEHICLES (veh_) ---
        # ========================================================
        # ----------------------------------------------------
        # VEHICLE OFFER & NEGOTIATION
        # ----------------------------------------------------
        "LIST_VEHICLE": "🚗 List a Vehicle",
        "FIND_VEHICLE": "🔍 Find a Vehicle",
        "vehicle.btn.return_main": "🏠 Return to Main Menu",
        "vehicle.btn.back": "⬅️ Back",
        "vehicle.btn.cancel": "❌ Cancel",
        "vehicle.btn.submit": "✅ Submit Request",
        "vehicle.offer.prompt": "🤝 **Make an Offer**\n\nPlease enter your initial offer in ETB (numbers only).",
        "vehicle.btn.cancel_offer": "❌ Cancel Offer",
        "vehicle.error.invalid_number": "⚠️ Invalid amount. Please enter numbers only.",
        "vehicle.error.listing_unavailable": "⚠️ This vehicle listing is no longer available.",
        "vehicle.error.cannot_offer_own": "⚠️ You cannot make an offer on your own vehicle.",
        "vehicle.error.system_offline": "⚠️ The negotiation system is currently offline. Please try again later.",
        "vehicle.offer.sent_success": "✅ Your offer of **{initial_offer} ETB** has been sent to the provider. You "
                                      "will be notified when they respond.",
        "vehicle.offer.sent_fail": "❌ Failed to send your offer. Please try again.",

        # ----------------------------------------------------
        # MAIN PIPELINE: LISTING & FINDING
        # ----------------------------------------------------
        "vehicle.list.welcome": "🚗 **List a Vehicle**\n\nAre you looking to sell or rent out your vehicle?",
        "vehicle.btn.sell_vehicle": "💰 Sell Vehicle",
        "vehicle.btn.rent_out": "🔑 Rent Out",
        "vehicle.find.welcome": "🔍 **Find a Vehicle**\n\nAre you looking to buy or rent a vehicle?",
        "vehicle.btn.buy_vehicle": "💰 Buy Vehicle",
        "vehicle.btn.rent_lease": "🔑 Rent / Lease",

        # Vehicle Types
        "vehicle.vtype.title": "⚡ **SMART MATCH VEHICLE CATEGORY** ⚡",
        "vehicle.vtype.sedan": "🚗 Sedan",
        "vehicle.vtype.suv": "🚙 SUV / SAV",
        "vehicle.vtype.van": "🚐 Van",
        "vehicle.vtype.bus": "🚌 Bus / Minibus",
        "vehicle.vtype.truck": "🚚 Truck",
        "vehicle.vtype.moto": "🏍️ Motorcycle",
        "vehicle.list.vtype_prompt": "Please select the vehicle category you wish to list:",
        "vehicle.find.vtype_prompt": "Please select the vehicle category you wish to find:",

        # Make, Model, Year
        "vehicle.list.make_model_prompt": "🏷️ ** Make, Model, and Year of Manufacture**\n\nPlease enter the "
                                          "vehicle's make, model, and year of manufacture (e.g., Toyota Doulfine "
                                          "Minibus 2LT 2000).",
        "vehicle.find.make_model_prompt": "🏷️ **Target  Make, Model, and Year of Manufacture**\n\nPlease type the "
                                          "specific vehicle's make, model, and year of manufacture you need (e.g., "
                                          "Toyota Doulfine Minibus 2LT 2000).",
        "vehicle.error.input_too_short": "⚠️ Input too short. Please provide a more detailed make and model.",

        # Condition
        "vehicle.list.condition_prompt": "⏳ **Condition / Years Used**\n\nHow many years has this vehicle been used? "
                                         "Enter '0' if it is brand new.",
        "vehicle.find.condition_prompt": "⏳ **Condition / Max Years Used**\n\nWhat is the maximum number of years the "
                                         "vehicle can be used? Enter '0' for brand new.",
        "vehicle.error.invalid_format_number": "⚠️ Invalid format. Please enter a valid whole number for the years.",

        # Fuel Types
        "vehicle.fuel.title": "⛽ **Fuel Type**",
        "vehicle.fuel.benzine": "⛽ Benzine (Petrol)",
        "vehicle.fuel.diesel": "🛢️ Diesel",
        "vehicle.fuel.electric": "⚡ Electric (EV)",
        "vehicle.fuel.lng_ngv": "🌐 LNG / NGV (Natural Gas)",
        "vehicle.fuel.hybrid_petrol": "🔋 Petrol-Electric Hybrid",
        "vehicle.fuel.hybrid_diesel": "🔋 Diesel-Electric Hybrid",
        "vehicle.fuel.hybrid_flex": "🌱 Flex-Fuel (Ethanol) Hybrid",
        "vehicle.fuel.hybrid_gaseous": "🔥 Bi-Fuel (Gas-Electric) Hybrid",
        "vehicle.fuel.hybrid_hydrogen": "💧 Hydrogen Fuel Cell Hybrid",
        "vehicle.list.fuel_prompt": "Select the vehicle's primary fuel type:",
        "vehicle.find.fuel_prompt": "Select your preferred fuel type:",

        # Features & Specs
        "vehicle.keyword.none": "none",
        "vehicle.list.features_prompt": "✨ **Features & Specs**\n\nList key features separated by commas (e.g., AC, "
                                        "Manual Transmission, Backup Camera). Type 'None' if standard.",
        "vehicle.find.features_prompt": "✨ **Required Features**\n\nList must-have features separated by commas ("
                                        "e.g., Automatic Transmission, AC). Type 'None' if you have no strict "
                                        "requirements.",

        # Location Data
        "vehicle.list.location_prompt": "📍 **Location**\n\nWhere is the vehicle currently located? Share your "
                                        "location (📎) or type the country, city/neighborhood.",
        "vehicle.find.location_prompt": "📍 **Target Location**\n\nWhere do you need the vehicle? Share your location "
                                        "(📎) or type the type the country, city/neighborhood.",
        "vehicle.location.gps_pin": "GPS Coordinates Provided",

        # Pricing & Budget
        "vehicle.list.price_sell_prompt": "💵 **Selling Price**\n\nEnter your asking price specifying its currency as "
                                          "ETB/USD (e.g., 1,500,000 ETB, or 10,150 USD).",
        "vehicle.list.price_rent_prompt": "💵 **Daily Rental Rate**\n\nEnter your daily rental rate specifying its "
                                          "currency as ETB/USD (e.g., 3,000 ETB, or 50 USD).",
        "vehicle.find.budget_buy_prompt": "💵 **Maximum Purchase Budget**\n\nEnter your max budget specifying its "
                                          "currency as ETB/USD (e.g., 1,500,000 ETB, or 10,150 USD).",
        "vehicle.find.budget_rent_prompt": "💵 **Maximum Daily Rental Budget**\n\nEnter your max daily rental budget "
                                           "specifying its currency as ETB/USD (e.g., 3,000 ETB, or 50 USD).",
        "vehicle.error.invalid_price": "⚠️ Invalid price. Please enter numeric values only.",

        # Photos
        "vehicle.list.photo_prompt": "📸 **Vehicle Photo**\n\nPlease upload a clear exterior photo of the vehicle. "
                                     "Type 'Skip' to proceed without one.",
        "vehicle.photo.uploading": "⏳ Uploading to secure server...",
        "vehicle.photo.upload_timeout": "⚠️ Upload failed. Proceeding without a photo.",

        # Contact & Syncing
        "vehicle.error.timeout": "⚠️ Network timeout while connecting to Telegram. Your request was preserved; please "
                                 "try again.",
        "vehicle.error.telegram_api": "⚠️ Telegram communication failure occurred. Please try again.",
        "vehicle_seeker_request_selected": "📋 <b>Selected Seeker Request:</b>",
        "preferred_mmy": "🚘 <b>Preferred Make/Model/Year:</b>",
        "preferred_fuel": "⛽ <b>Preferred Fuel Type:</b>",
        "location_route": "📍 <b>Location / Route:</b>",
        "max_budget": "💰 <b>Max Budget:</b>",
        "select_option_offer": "👇 <b>Select an option below to submit your offer:</b>",
        'vehicle_selected': "✅ <b>Vehicle Selected</b>",
        'type': "📂 <b>Type:</b>",
        'listing_mmy': "🚗 <b>Listing Make/Model/Year:</b>",
        'listing_fuel': "⛽ <b>Listing Fuel Type:</b>",
        'location': "📍 <b>Location:</b>",
        'price': "💰 <b>Price:</b>",

        # Status & System Alerts
        "msg_request_inactive": "⚠️ <b>Selected seeker request is no longer active.</b>",
        "msg_vehicle_inactive": "⚠️ <b>Selected vehicle is no longer active.</b>",
        # Section Headers
        "seeker_match_details_header": "👤 SEEKER MATCH DETAILS",
        "vehicle_option_details_header": "🚗 VEHICLE OPTION DETAILS",
        # Property & Field Labels
        "lbl_your_listed_vehicle": "Your Listed Vehicle",
        "lbl_requested_spec": "🎯 Requested Spec:",
        "lbl_proposed_spec": "🚙 Your Proposed Spec:",
        "lbl_your_requested_spec": "🎯 Your Requested Spec:",
        "lbl_offered_vehicle_spec": "🚜 Offered Vehicle Spec:",
        "lbl_fuel_type": "⛽ Fuel Type:",
        "lbl_your_vehicle": "Your Vehicle",
        "lbl_location_route": "📍 Location/Route:",
        "lbl_location": "📍 Location:",
        "lbl_seeker_budget": "💰 Seeker Budget:",
        "lbl_flexible": "Flexible",
        # Action Prompts & Footers
        "match_flexibility_note": "💡 Note: This match allows proposing your vehicle even if specifications vary "
                                  "slightly.",

        'select_option_owner': "Select an option below to submit your offer to the owner:",
        'enter_custom_offer_btn': "🤝 Present your own offer",
        "send_asking_rate": "✅ Send Asking Rate ({price} {currency})",
        "accept_asking_rate": "✅ Accept Rate ({price} {currency})",
        'back_to_matches': "❌ Back to Top Matches",
        'decline_deal': "❌ Decline Deal",
        'prompt_offer_title': "💬 <b>Enter Your Proposed Offer</b>",
        'prompt_seeker_offer_title': "💬 <b>Enter Your Proposed Offer for this Seeker</b>",
        'seeker_request_label': "👤 Request:",
        'seeker_budget_label': "💰 Seeker Budget:",
        'asking_rate_label': "💰 Asking Rate:",
        'prompt_reply_instructions': "Please reply with your offer amount in {currency} (numbers only, e.g., "
                                     "<code>5000</code>):",
        'invalid_amount': "⚠️ <b>Invalid amount format.</b>\nPlease enter numbers only.\n*(Example: 5000)*",

        'transmitting': "📡 <i>Transmitting offer and generating deal session...</i>",
        'err_seeker_listing': "⚠️ <b>System Error:</b> Missing Seeker or Listing ID for Reverse Match.",
        'err_provider': "⚠️ <b>System Error:</b> Could not verify vehicle provider.",
        'seeker_proposal_title': "📩 <b>NEW VEHICLE PROPOSAL RECEIVED</b>",
        'seeker_proposal_desc': "A vehicle owner has proposed an offer for your active request!",
        'proposed_rate': "💰 <b>Proposed Rate:</b>",
        'requested_veh': "🎯 <b>Requested:</b>",
        'offered_veh': "🚗 <b>Offered:</b>",
        'note_diff': "💡 <i>Review specs carefully before accepting.</i>",

        'forward_offer_transmitted': "✅ <b>Offer Transmitted!</b>\n\nYour proposed rate of <b>{formatted_price}</b> "
                                     "for <i>{asset_title}</i> has been securely forwarded.",
        'provider_offer_title': "📩 <b>NEW OFFER ON YOUR VEHICLE</b>",
        'provider_offer_desc': "A seeker submitted an offer on your vehicle listing!",
        'offered_amount': "💰 <b>Offered Amount:</b>",
        'listed_veh': "📝 <b>Listed Vehicle:</b>",
        'seeker_desired_veh': "🎯 <b>Seeker Desired:</b>",

        'accept_btn': "✅ Accept Offer",
        'counter_btn': "💬 Counter Offer",
        'decline_btn': "❌ Decline",

        'deal_blocked': "⚠️ <b>Could not initiate deal session. Please try again.</b>",
        'system_err': "⚠️ <b>A system error occurred. Please try again later.</b>",

        'new_provider_offer_title': "🔔 <b>New Provider Offer Received!</b>",
        'new_provider_offer_intro': "A provider has proposed a rate of <b>{formatted_price}</b>.",
        'requested_specs_header': "📌 <b>Your Requested (Preferred) Specs:</b>",
        'offered_specs_header': "🚗 <b>Offered Listing Specs:</b>",
        'spec_mmy': "  • Make/Model/Year: {val}",
        'spec_fuel': "  • Fuel Type: {val}",
        'choose_action': "Please choose an action below to respond:",
        'accept_price_btn': "✅ Accept Price",
        'counter_offer_btn': "📉 Counter Offer",
        # UPDATE THIS TRANSLATION KEY:
        "transmitting_offer": "🔄 <i>Transmitting offer and generating deal session...</i>",
        "offer_transmitted": "✅ <b>Offer Transmitted!</b>\n\nYour proposed rate of <b>{formatted_price}</b> for <i>{"
                             "asset_title}</i> has been securely forwarded.",
        "provider_offer_transmitted": "✅ <b>Offer Transmitted Successfully!</b>\n\nYour offer of <code>{"
                                      "formatted_price}</code> for <b>{list_mmy}</b> has been sent to the Seeker.",

        'new_offer_on_listing_title': "🔔 <b>New Offer on Your Listing!</b>",
        'new_offer_on_listing_intro': "A seeker has proposed an offer of <b>{formatted_price}</b>.",
        'your_listing_specs_header': "🚗 <b>Your Listing Specs:</b>",
        'seekers_requested_specs_header': "📌 <b>Seeker's Requested Specs:</b>",
        'accept_offer_btn': "✅ Accept Offer",
        'negotiation_blocked': "🚫 <b>Negotiation Blocked</b>\n\nThis asset is currently under active negotiation.",
        'system_error': "⚠️ System Error: Could not initiate negotiation.",
        "vehicle.list.contact_prompt": "📞 **Contact Number**\n\nEnter your phone number for verified clients (e.g., "
                                       "0911234567).",
        "vehicle.find.contact_prompt": "📞 **Contact Number**\n\nEnter your phone number for verified providers ("
                                       "e.g., 0911234567).",
        "vehicle.sync.fleet_records": "⚙️ Synchronizing with the Smart Match fleet database...",
        "vehicle.list.success": "🎉 **Vehicle Listed!**\n\nYour {year_model} ({vtype}) is now active on the platform.",
        "vehicle.find.interrogating": "🔍 Scanning nodes and interrogating the match matrix...",
        "vehicle.find.no_match": "📡 Your request is broadcasted. No immediate matches found, but we will notify you "
                                 "when one arrives.",

        # Escrow & Match Output
        "MSG_TOP_VEHICLE_SEEKER_MATCHES": "🎯 <b>Top {count} Active {vertical} Seekers "
                                          "Found!</b>\n──────────────────────────────\nThese seekers are requesting a "
                                          "vehicle. Select a seeker request below to propose your vehicle:",
        # --- DOMAIN / VERTICAL NAMES ---
        "domain_housing": "Housing",
        "domain_vehicle": "Vehicle",
        "domain_equipment": "Equipment",

        "vehicle.btn.lock_vehicle": "🔐 Lock Deal & Pay",
        "vehicle.btn.decline_match": "❌ Decline Match",
        "vehicle.label.daily_rental": "Daily Rate",
        "vehicle.label.purchase_price": "Asking Price",
        "vehicle.find.match_found": "🎯 **Match Found!** (Score: {score}%)\n\n**Vehicle:** {asset}\n**Fuel:** {"
                                    "fuel}\n**Location:** {location}\n**{price_label}:** {price} ETB\n\nWould you "
                                    "like to secure this vehicle?",
        "vehicle.error.pipeline_crash": "❌ A pipeline anomaly occurred. Please restart the process.",
        "vehicle.escrow.syncing": "⚙️ Generating secure escrow invoice...",
        "vehicle.btn.open_portal": "💳 Open Payment Portal",
        "vehicle.escrow.initialized": "🔐 **Escrow Initialized**\n\nPlease complete the platform fee payment to "
                                      "unlock the provider's direct contact details.",
        "vehicle.escrow.failed": "❌ Failed to generate the payment link. Please try again.",
        "vehicle.cancel_message": "🛑 Process cancelled. Returning to standby.",
        "vehicle.btn.send_offer": "🤝 Send Offer to Owner",
        "vehicle.find.top_matches_found": "🎯 We found these top matches for you. Please select one:",
        "vehicle.negotiation.syncing": "🔄 Transmitting offer to the vehicle owner...",
        "vehicle.negotiation.started": "✅ **Offer Sent!**\n\nYour request for `{price} ETB` has been transmitted to "
                                       "the vehicle owner. They will review your request and can **Accept**, "
                                       "**Counter**, or **Decline**.\n\nYou will receive a notification as soon as "
                                       "they respond.",

        # =========================================================
        # --- FREIGHT & RIDE (logistics_) ---
        # =========================================================
        # ----------------------------------------------------
        # GLOBAL NAVIGATION & OFFERS
        # ----------------------------------------------------
        "RETURN_MAIN_MENU": "Returning to the Main Menu...",
        "FREIGHT_OFFER_PROMPT": "🤝 **Make an Offer**\n\nPlease enter your initial offer in ETB (numbers only).",
        "BTN_CANCEL_OFFER": "❌ Cancel Offer",
        "ERR_INVALID_NUMBER": "⚠️ Invalid amount. Please enter positive numbers only.",
        "ERR_LISTING_UNAVAILABLE": "⚠️ This listing is no longer available.",
        "ERR_OWN_LISTING": "⚠️ You cannot make an offer on your own listing.",
        "ERR_SYS_NEGOTIATION_OFFLINE": "⚠️ The negotiation system is currently offline. Please try again later.",
        "MSG_OFFER_SENT": "✅ Your offer of **{amount} ETB** has been sent to the provider. You will be notified when "
                          "they respond.",
        "ERR_OFFER_FAILED": "❌ Failed to send your offer. Please try again.",
        "MSG_PROCESS_CANCELLED": "🛑 Process cancelled. Returning to the main menu.",

        # ----------------------------------------------------
        # ROLE SELECTION
        # ----------------------------------------------------
        "MSG_FREIGHT_START": "🚛 **Freight Logistics**\n\nAre you looking to ship goods or offer carrier capacity?",
        "BTN_ROLE_SHIPPER": "📦 Find Carrier (Shipper)",
        "BTN_ROLE_CARRIER": "🚚 Offer Capacity (Carrier)",

        "MSG_RIDE_START": "🚕 **Ride-Sharing**\n\nAre you a passenger looking for a ride or a driver offering one?",
        "BTN_ROLE_PASSENGER": "🧍 Find a Ride",
        "BTN_ROLE_DRIVER": "🚗 Offer a Ride (Driver)",

        # ----------------------------------------------------
        # ORIGIN & DESTINATION
        # ----------------------------------------------------
        "PROMPT_ORIGIN_SHIPPER": "📍 **Origin Location**\n\nWhere are the goods currently located?",
        "PROMPT_ORIGIN_CARRIER": "📍 **Base Location**\n\nWhere is your carrier currently stationed or operating from?",
        "PROMPT_ORIGIN_PASSENGER": "📍 **Pickup Location**\n\nWhere would you like to be picked up?",
        "PROMPT_ORIGIN_DRIVER": "📍 **Current Location**\n\nWhere are you currently starting your route?",
        "ERR_VAGUE_ADDRESS": "⚠️ Address too brief. Please provide a more specific location.",

        "PROMPT_DEST_SHIPPER": "📍 **Destination**\n\nWhere are the goods going from {origin}?",
        "PROMPT_DEST_CARRIER": "📍 Origin verified as {origin}. Please define your preferred destination sector or "
                               "coverage area:",
        "PROMPT_DEST_PASSENGER": "📍 **Destination**\n\nWhere are you heading to from {origin}?",
        "PROMPT_DEST_DRIVER": "📍 **Destination**\n\nWhere are you heading to from {origin}?",
        "ERR_INVALID_DESTINATION": "⚠️ Destination address too brief. Please be more specific.",
        "PROMPT_ENTER_OPERATING_REGION": "Please enter your operating region or preferred operating region (e.g., "
                                         "Somalia or Jijiga to Addis Ababa).",

        # ----------------------------------------------------
        # GPS TELEMETRY
        # ----------------------------------------------------
        "BTN_SHARE_GPS": "📍 Share GPS Location",
        "MSG_GPS_BYPASSED": "⚠️ GPS skipped. Using text address for routing.",
        "MSG_GPS_LOCKED": "🛰️ GPS Telemetry successfully synced.",
        "MSG_COORDS_LOCKED": "🛰️ Coordinates locked: **{lat:.6f}° N, {lon:.6f}° E**",
        "PROMPT_GPS_CALIBRATION": (
            "🛰️ **GPS Calibration**\n\n"
            "• **Mobile users:** Tap the button below to share your GPS location.\n"
            "• **Desktop / Manual:** Type your neighborhood or paste coordinates\n"
            "  *(e.g., `Latitude 8.514477° N, Longitude 39.269257° E` or `8° 30' 52\" N, 39° 16' 9\" E`)*."
        ),

        # ----------------------------------------------------
        # CAPACITY & SPECS
        # ----------------------------------------------------
        "PROMPT_CAPACITY_SHIPPER": "⚖️ **Cargo Weight**\n\nWhat is the estimated weight of your cargo in kilograms ("
                                   "kg)? (Numbers only)",
        "PROMPT_CAPACITY_CARRIER": "⚖️ **Max Capacity**\n\nWhat is the maximum weight your vehicle can carry in "
                                   "kilograms (kg)? (Numbers only)",
        "PROMPT_CAPACITY_PASSENGER": "👥 **Party Size**\n\nHow many passengers are traveling? (Numbers only)",
        "PROMPT_CAPACITY_DRIVER": "💺 **Available Seats**\n\nHow many seats are available in your vehicle? (Numbers "
                                  "only)",
        "ERR_INVALID_NUMBER_CLEAN": "⚠️ Invalid format. Please enter a valid whole number.",

        # ----------------------------------------------------
        # VEHICLE CLASSES & EXTRAS
        # ----------------------------------------------------
        "PROMPT_CLASS_FREIGHT": "🚛 **Trailer Type/Vehicle Class**\n\nWhat specific vehicle or equipment type is "
                                "required or offered? (e.g., Bajaj Cargo, Isuzu FSR, FSR, Dry Van, Flatbed, "
                                "Refrigerated, Box Truck)",
        "PROMPT_CLASS_RIDE": "🚘 **Ride Class/Vehicle Type**\n\nWhat is the preferred or offered vehicle class? ("
                             "e.g., Economy, Comfort, Minivan)",
        "ERR_DESC_TOO_BRIEF": "⚠️ Description too brief. Please provide a clearer vehicle class.",

        "PROMPT_EXTRAS_FREIGHT": "🚀 **Special Requirements**\n\nList any special handling needs or capacity "
                                 "separated by commas (e.g., Fragile, Hazmat, Loading assistance). Type 'None' if "
                                 "standard.",
        "PROMPT_EXTRAS_RIDE": "✨ **Amenities & Extras**\n\nList any required/offered amenities separated by commas ("
                              "e.g., AC, Pet-friendly, Extra Luggage). Type 'None' if standard.",
        "KEYWORD_HAZMAT": "hazmat",
        "KEYWORD_NONE": "none",

        # ----------------------------------------------------
        # BUDGET & CONTACT
        # ----------------------------------------------------
        "PROMPT_BUDGET_SHIPPER": "💵 **Shipping Budget**\n\nEnter your estimated quote for this route in ETB or USD ("
                                 "e.g., 5000 ETB or 50 USD/$50).",
        "PROMPT_BUDGET_CARRIER": "💵 **Shipping Quote**\n\nEnter your estimated quote for this route in ETB or USD ("
                                 "e.g., 5000 ETB or 50 USD/$50).",
        "PROMPT_BUDGET_PASSENGER": "💵 **Max Fare Budget**\n\nEnter your maximum budget for this ride in ETB or USD ("
                                   "e.g., 5000 ETB or 50 USD/$50).",
        "PROMPT_BUDGET_DRIVER": "💵 **Estimated Fare**\n\nEnter your required fare for this trip in ETB or USD (e.g.,"
                                " 5000 ETB or 50 USD/$50).",
        "ERR_INVALID_BUDGET": "⚠️ Invalid amount. Please enter numeric values only.",
        "MSG_REQUEST_PLATE": "🚗 Please enter your official vehicle plate registration number (e.g., AA-3-B12345):",
        "ERR_INVALID_PLATE": "⚠️ Invalid plate registration format. Please enter a valid alphanumeric plate number:",
        "prompt_vehicle_photo": "📸 Please upload a clear photo of your Vehicle or type 'Skip' to continue without a "
                                "photo:",
        "prompt_asset_photo": "📸 Please upload a clear photo of your Asset/Vehicle or type 'Skip' to continue "
                              "without a photo:",
        "PROMPT_CONTACT_INFO": "📞 **Contact Number**\n\nEnter your active phone number (e.g., 0911234567).",

        # ----------------------------------------------------
        # PIPELINE, MATCHING & CHECKOUT
        # ----------------------------------------------------
        # General & Status Messages
        "MSG_COMPILING_PROFILE": "⏳ Compiling profile and syncing logistics nodes...",
        "MSG_SCANNING_NODES": "🔍 Scanning <b>{domain}</b> nodes and interrogating the match matrix...",
        "MSG_REGISTRATION_COMPLETE": "✅ **{domain} Asset Registration Complete!**",
        "MSG_REQUEST_BROADCASTED": "📡 Request broadcasted to the {domain} network. No immediate matches found, "
                                   "but we will notify you when a match is secured.",
        "MSG_TOP_FREIGHT_MATCHES": "🎯 **Top {count} {domain} Matches Found!**\n\nPlease select an option below to "
                                   "review and send an offer:",

        # Domains
        "domain_freight": "Freight",
        "domain_ride": "Ride",

        # Buttons & Labels
        "BTN_DECLINE_RETURN": "❌ Decline & Return",

        # Seeker Match Items
        "MSG_CARD_SEEKER_SELECTED_FREIGHT": "👤 <b>Seeker Request Selected:</b> {title}\n📍 <b>Route:</b> <code>{"
                                            "origin} ➡️ {destination}</code>\n{details}💰 <b>Max Budget:</b> <code>{"
                                            "budget} {currency}</code>\n\n<i>Select an option below to submit your "
                                            "offer to this seeker:</i>",
        "BTN_ACCEPT_DEAL": "✅ Accept Offer",
        "BTN_COUNTER_OFFER": "📉 Counter Offer",
        "BTN_DECLINE_DEAL": "❌ Decline",
        "NEGOTIATION_TITLE": "🔄 New Offer Received",
        "TRANSACTION_LABEL": "Transaction",
        "ASSET_LABEL": "Asset/Item",
        "OFFERED_PRICE": "Offered Price",
        "EVALUATE_PROMPT": "Please evaluate the offered rate and select an action below.",
        "BTN_ACCEPT_MATCH": "🔐 Accept & Lock",
        "MSG_MATCH_FOUND": "🎯 **Match Found!** (AI Confidence: {score}%)\n\n**Estimated Fare:** {fare}\n\nWould you "
                           "like to lock in this match?",
        "ERR_SYS_PIPELINE_FAILED": "⚠️ An error occurred during the matching pipeline. Please try again.",
        "MSG_TRANSMITTING_OFFER": "🔄 Transmitting offer to the provider...",

        "MSG_SECURE_CHECKOUT_LOAD": "⚙️ Generating secure escrow/billing invoice...",
        "BTN_PAYMENT_PORTAL": "💳 Open Payment Portal",
        "MSG_LOGISTICS_LOCKED": "🔐 **Deal Locked**\n\nPlease complete the platform fee or escrow payment to unlock "
                                "direct contact details.",
        "ERR_CHECKOUT_FAILED": "❌ Failed to generate the payment link. Please try again later.",

        "err_vehicle_not_found": "⚠️ Selected vehicle is no longer active.",
        "MSG_SELECTED_MATCH_SUMMARY": "{domain_emoji} <b>Selected Match Summary:</b>\n<i>{asset_title}</i>\n\n💰 "
                                      "<b>Estimated Fare:</b> <code>{fare} ETB</code>\n\nSelect an option below to "
                                      "submit your offer:",
        "msg_enter_custom_offer": "💬 <b>Enter Your Proposed Offer</b>",
        "MSG_PROMPT_CUSTOM_OFFER": "{header_text}\n\n<b>Logistics Profile:</b>\n<i>{asset_title}</i>\n\nEstimated "
                                   "Fare: <code>{asking_price} ETB</code>\n\nPlease reply with your offer amount in "
                                   "ETB (e.g., <code>125000</code> or <code>125,000</code>):",
        "err_invalid_price": "⚠️ <b>Invalid Amount Format</b>\n\nPlease reply with numbers only (e.g., "
                             "<code>1500</code> or <code>125,000</code>).",
        "MSG_OFFER_TRANSMITTING": "⏳ <b>Transmitting Offer...</b>\n\nYour offer of <code>{custom_offer} ETB</code> is "
                                  "being sent to the provider. Please wait while they review it.",
        "ERR_VERIFY_PROVIDER": "⚠️ System Error: Could not verify service provider.",
        "MSG_OFFER_TRANSMITTED_SUCCESS": "✅ <b>Offer Transmitted!</b>\n\nYour proposed rate of <b>{formatted_price} "
                                         "ETB</b> for <i>{safe_asset_title}</i> has been securely forwarded to the "
                                         "provider.",
        "ERR_PENDING_NEGOTIATION": "🚫 <b>Negotiation Blocked</b>\n\nThis provider is currently reviewing another "
                                   "negotiation. Please try again later.",
        "ERR_DEAL_INIT_FAILED": "⚠️ System Error: Could not initiate the deal negotiation.",
        "MSG_PROPOSE_OFFER_HEADER": "Propose an Offer",
        "LBL_ASSET": "Asset / Item",
        "LBL_ASKING_RATE": "Asking Rate / Price",
        "MSG_PROPOSE_OFFER_INSTRUCTION": "Review the details above to accept the price or make an offer.",

        # Verticals
        "vertical_housing": "Housing",
        "vertical_vehicle": "Vehicle",
        "vertical_equipment": "Equipment",
        "vertical_freight": "Freight",
        "vertical_ride": "Ride",

        # Transactions
        "tx_rental": "Rental",
        "tx_sale": "Sale",
        "tx_lease": "Lease",

        # Logistics Labels
        "lbl_route": "Route",
        "lbl_plate": "Plate Number",
        "lbl_weight": "Cargo Weight",
        "lbl_party": "Party Size",
        "lbl_category": "Category",
        "lbl_transaction": "Transaction",

        "LABEL_ROUTE": "Route",
        "LABEL_PLATE": "Plate",
        "LABEL_WEIGHT": "Weight",
        "LABEL_PARTY_SIZE": "Party Size",

        # --- Requests & Processing ---
        "SYS_PROCESSING": "⏳ <b>Processing your request...</b>\n\n<i>Please hold on while we securely initialize the "
                          "negotiation stream and generate payment links.</i> 🔄",
        "ERR_MISSING_SEEKER_LISTING": "⚠️ System Integrity Fault: Missing verified Seeker or Listing ID for Reverse "
                                      "Mapping.",
        "ERR_NEGOTIATION_UNAVAILABLE": "⚠️ System Error: Negotiation service is currently unavailable.",
        "ERR_SYNC_STREAM": "⚠️ System Error: Unable to initiate synchronized negotiation stream.",

        # --- Success Messages ---
        "SUCCESS_PROVIDER_OFFER": (
            "✅ <b>Provider Offer Transmitted!</b>\n\n"
            "🏠 <b>Asset/Request:</b> <i>{escaped_title}</i>\n"
            "💰 <b>Proposed Rate:</b> <b>{formatted_price} {currency}</b>\n\n"
            "Your fulfillment rate has been successfully relayed to the seeker node."
        ),
        "SUCCESS_DEAL_BID": (
            "✅ <b>Deal Bid Transmitted!</b>\n\n"
            "🏠 <b>Asset/Service:</b> <i>{escaped_title}</i>\n"
            "💰 <b>Proposed Commitment:</b> <b>{formatted_price} {currency}</b>\n\n"
            "Your commitment rate has been securely forwarded into the negotiation module."
        ),

        # --- Pipeline Blocks ---
        "BLOCK_NEGOTIATION": (
            "🚫 <b>Negotiation Pipeline Blocked</b>\n\n"
            "This target asset structure is strictly engaged in an active negotiation block, or you are attempting to"
            " offer on your own listing."
        ),

        # --- Cards & Detail Labels ---
        "LABEL_CARGO_WEIGHT": "Cargo Weight",

        # --- Common Values / Fallbacks ---
        "VAL_NEGOTIABLE": "Negotiable",
        "VAL_ORIGIN_DEFAULT": "Origin Point",
        "VAL_DEST_DEFAULT": "Destination Point",

        # Notifications & Templates
        "deal_agreed_provider_confirmation": (
            "✅ You accepted the offer of <b>{price} {currency}</b> for <i>{asset_title}</i>. "
            "Awaiting seeker payment.\n{details}"
        ),
        "deal_agreed_seeker_invoice": (
            "🎉 <b>Offer Accepted!</b>\n\n"
            "The provider accepted your offer of <b>{price} {currency}</b> for <i>{asset_title}</i>.\n"
            "{details}\nTo view full details and provider contact information (*phone number*), please pay the "
            "platform commission."
        ),
        "deal_agreed_seeker_confirmation": (
            "✅ <b>Counter-Offer Accepted!</b>\n\n"
            "You accepted the counter-offer of <b>{price} {currency}</b> for <i>{asset_title}</i>.\n"
            "{details}\nTo view full details and provider contact information (*phone number*), please pay the "
            "platform commission."
        ),
        "deal_agreed_provider_notice": (
            "🎉 <b>Counter-Offer Accepted!</b>\n\nThe seeker accepted your counter-offer of "
            "<i>{asset_title}</i> for <b>{price} {currency}</b>.\n{details}\n"
            "We are currently waiting for the seeker to fund the escrow."
        ),
        "btn_pay_local": "🇪🇹 Local Wallets ({price} ETB)",
        "btn_pay_intl": "🌍 International Cards / PayPal",

        # ----------------------------------------------------
        # WEBHOOKS
        # ----------------------------------------------------
        "DEFAULT_DRIVER_NAME": "Platform Driver/Carrier",
        "MSG_WEBHOOK_SUCCESS": "✅ **Payment Confirmed!**\n\nDeal ID: `{deal_id}`\n\n**Driver/Carrier:** {"
                               "driver_name}\n**Contact:** {contact}\n**Plate:** {vehicle_plate}\n\nThank you for "
                               "using the platform.",

        # =========================================================
        # --- MATCHING & DEALS (deal_) ---
        # =========================================================
        "deal_match_found": "🎉 *Match Found!*\n\nWe found a potential counterparty for your request.",
        "deal_proposed": "🤝 *New Deal Proposed!*\n\nReview the details below:",
        "deal_btn_accept": "✅ Accept Deal",
        "deal_btn_decline": "❌ Decline Deal",
        "deal_accepted_seeker": "✅ You accepted the deal! Awaiting provider confirmation...",
        "deal_accepted_provider": "🎉 *Deal Confirmed!* Both parties have accepted. Here is the contact info:",
        "deal_declined": "🚫 The deal was declined. We will keep looking for other matches.",
        "deal_contact_info": "📞 *Contact Information:*\nName: {name}\nPhone: {phone}",

        "TITLE_TRANSPORT_ROUTE_FREIGHT": "Transport Route: {origin} ➡️ {destination}",
        "MSG_CARD_SEEKER_SELECTED": (
            "👤 <b>Seeker Request Selected:</b> {title}\n"
            "📍 <b>Route:</b> <code>{origin} ➡️ {destination}</code>\n"
            "💰 <b>Max Budget:</b> <code>{budget} {currency}</code>\n\n"
            "<i>Select an option below to submit your offer to this seeker:</i>"
        ),
        "MSG_CARD_MATCH_SUMMARY_FREIGHT": (
            "🚚 <b>Selected Match Summary:</b>\n\n"
            "{title}\n"
            "📍 <b>Route:</b> {origin} ➡️ {destination}\n"
            "💰 <b>Estimated Fare:</b> <b>{fare} {currency}</b>\n\n"
            "Select an option below to submit your transport bid:"
        ),
        "BTN_SEND_ASKING_RATE": "✅ Send Asking Rate ({price} {currency})",
        "BTN_ACCEPT_FARE": "✅ Accept Fare ({fare} {currency})",
        "BTN_BACK_TOP_MATCHES": "❌ Back to Top Matches",
        "ERR_REQUEST_NOT_FOUND": "⚠️ Selected request is deprecated or resolved.",
        "ERR_VEHICLE_NOT_FOUND": "⚠️ Selected vehicle match is no longer available in the registry.",

        # REVERSE_MATCH_STRINGS
        "scanning": "🔍 Scanning active Seeker Requests across the matrix...",
        "no_matches": "✅ **Listing is Live!**\nCurrently, no pending seekers explicitly request this class. You will "
                      "be notified automatically.",
        "top_seekers": "🎯 **Top {count} Seekers Found!**\nThese users are actively requesting transport or service "
                       "logic matching your asset. Select a Seeker below to send your offer:",
        "btn_match": "👤 #{index} Seeker (Budget: {budget} {currency} - {score}%)",
        "btn_skip": "⏭️ Skip & Return to Menu",
        "error_skipped": "✅ Listing created successfully. (Reverse scan skipped due to system load).",
        "MSG_TOP_SEEKER_MATCHES": "🎯 **Top {count} {vertical} Seeker Matches Found!**\n\nThese users are actively "
                                  "requesting transport or service logic matching your asset. Select a Seeker below "
                                  "to send your offer:",
        "BTN_SEEKER_MATCH_ITEM": "👤 #{index} Seeker (Budget: {budget} {currency} - {score}%)",

        # ==========================================
        # DEALS HANDLER
        # ==========================================
        "prompt_enter_counter_price": "Please enter your counter offer price:",
        "btn_cancel_counter": "❌ Cancel Counter Offer",
        "err_action_required_counter": (
            "⚠️ **Action Required**\n\n"
            "It looks like you are trying to submit a counter-offer price. "
            "Please click the **'📉 Counter Offer'** button directly on the deal notification card first, "
            "then type your price when prompted."
        ),
        "err_sys_general": "⚠️ System error occurred while processing your request. Please try again.",

        "error_system_state": "⚠️ System error: Lost negotiation state. Please start over.",
        "error_invalid_numeric_input": "⚠️ Invalid input. Please enter a valid positive number for the price.",
        "error_deal_expired": "⚠️ This deal has expired or is no longer in the negotiating state.",
        "notice_provider_countered": "🔔 The provider has countered your offer. New price: *{price} ETB*",
        "notice_seeker_countered": "🔔 The buyer has countered your offer. New price: *{price} ETB*",
        "btn_counter_offer": "💬 Counter Offer",
        "btn_decline_deal": "🚫 Decline Deal",
        "success_counter_transmitted": "✅ Your counter offer of *{price} ETB* has been securely transmitted.",
        "error_unexpected": "⚠️ An unexpected error occurred. Please try again.",
        "notice_counter_aborted": "❌ Counter offer aborted. The negotiation remains active at the previous price.",
        "notice_action_cancelled": "❌ Action cancelled successfully.",
        "error_transaction_invalid": "⚠️ Invalid transaction request. The deal may have been closed.",
        "error_execution": "⚠️ Failed to execute the requested action. Please contact support.",
        "btn_pay_commission": "💳 Pay Commission",
        "notice_deal_declined_self": "🚫 You have successfully declined the deal. The negotiation is now closed.",
        "notice_deal_declined_opposing": "🚫 The opposing party has declined the current offer. This negotiation has "
                                         "been closed.",
        "msg_missed_alerts": "🔔 **While you were busy, you received the following updates:**",
        "lbl_queued_notification": "🔔 Notification",
        "msg_queue_cleared_summary": "✅ **All pending updates have been delivered.**",
        "err_queue_delivery_failed": "⚠️ *Could not deliver a queued item. Please refresh your session.*",
        "msg_queue_overflow": "⚠️ **Notice:** Some older updates were trimmed to keep your inbox manageable.",

        "error_billing_pipeline": "⚠️ Billing service failed.",
        "msg_negotiation_cancelled": "❌ Action cancelled.",
        "err_unauthorized_action": "⚠️ Unauthorized action. You are not a party to this deal.",
        "err_sys_invalid_price": "⚠️ System Error: Invalid deal price. Please contact support.",

        # --- Notification Templates ---
        "notice_new_offer_template": (
            "🔔 *New {tx_type} Offer ({vertical_type})!*\n\n"
            "🏷️ *Transaction:* {tx_type}\n"
            "🏠 *Asset:* _{asset_title}_\n"
            "{attributes}"
            "💰 *Offered Price:* *{price} {currency}*"
        ),
        "notice_reverse_offer_template": (
            "🔄 *New Reverse Match Offer ({vertical_type})!*\n"
            "The provider offered a price on your service request:\n"
            "🏷️ *Transaction:* {tx_type}\n"
            "🏠 *Asset:* _{asset_title}_\n"
            "{attributes}\n"
            "💰 *Offered Price:* *{price} {currency}*"
        ),

        "notice_offer_sent_seeker": "✅ Your offer of *{price} {currency}* has been sent to the provider!",
        "notice_reverse_offer_sent_provider": "✅ Your offer of *{price} {currency}* has been sent to the seeker!",

        # --- Localized Terms ---
        "verticals": {
            "housing": "Housing",
            "vehicle": "Vehicle",
            "equipment": "Equipment",
            "freight": "Freight",
            "ride": "Ride",
            "general": "Asset"
        },
        "transactions": {
            "rental": "Rental",
            "rent": "Rental",
            "sale": "Sale",
            "lease": "Lease",
            "service": "Service",
            "booking": "Booking"
        },

        "msg_counter_prompt": "Please enter your counter offer amount in currency:",
        "msg_counter_sent": "✅ Counter offer of {price} {currency} sent successfully!",
        "notice_reverse_offer_sent_seeker": "✅ Your offer of *{price} {currency}* has been sent to the seeker!",

        "deal_agreed_provider_counter_accepted": "✅ You accepted the counter-offer of *{price} {currency}* for _{"
                                                 "asset_title}_. Awaiting seeker's commission payment.\n{details}",
        "deal_agreed_seeker_counter_accepted": "🎉 *Counter-Offer Accepted!*\n\nThe provider accepted your "
                                               "counter-offer of *{price} {currency}* for _{asset_title}_.\n{"
                                               "details}\nTo access detailed information and contact details, "
                                               "please process the commission payment below.",
        "deal_agreed_seeker_offer_accepted": "✅ *Offer Accepted!*\n\nYou accepted the offer of *{price} {currency}* "
                                             "for _{asset_title}_.\n{details}\nTo access detailed information and "
                                             "contact details, please process the commission payment below.",
        "deal_agreed_provider_offer_accepted": "🎉 *Offer Accepted!*\n\nThe seeker accepted your offer of *{price} {"
                                               "currency}* for _{asset_title}_.\n{details}\nWe are awaiting the "
                                               "seeker's commission payment.",

        # ====================================================================
        # --- VERIFICATION (verify_) ---
        # ====================================================================
        "verify_prompt": "🛡️ To keep our platform safe, please upload a clear photo of your National ID or Driver's "
                         "License.",
        "verify_pending": "⏳ Your ID is being verified by our AI engine. Please wait...",
        "verify_success": "✅ Verification successful! Your account is now trusted.",
        "verify_failed": "❌ Verification failed. Please ensure the image is clear and try again.",
        "verify_start_message": "🛡️ **Smart Match Africa Verification**\n\nTo ensure platform safety, prevent fraud, "
                                "and unlock freight/passenger matching, we require identity verification.\n\nPlease "
                                "upload a clear, high-resolution **photo of your official Driver's License or "
                                "Government ID**.",
        "error_invalid_input_photo_required": "⚠️ **Invalid Input Type**\n\nPlease tap the attachment icon (📎) and "
                                              "send a clear *photo* of your physical identification card.",
        "verify_processing_document": "🔒 **Document Received!**\nUploading to our secure sandbox and enqueuing "
                                      "background AI analysis. Please wait...",
        "verify_upload_failed": "❌ System busy. We could not register your upload stream. Please try again with "
                                "/verify later.",
        "verify_success_message": "✅ **Verification Successful!**\n\nYour driver profile is now active, "
                                  "fully verified, and trusted by the Smart Match Africa matching grid.",
        "verify_failed_validation": "❌ **Verification Failed**\n\nThe uploaded document was either unreadable, "
                                    "missing required security markers, or rejected by our validation "
                                    "models.\n\nPlease tap /verify to restart the process with a sharper, "
                                    "well-lit photo.",
        "error_system_timeout": "⚠️ **System Timeout**\n\nWe encountered a temporary connection error while "
                                "communicating with our verification sandbox. Please try again later with /verify.",
        "verify_cancelled_message": "🛑 **Verification Paused**\n\nYou have exited the setup sequence. You can return "
                                    "and verify your identity at any time by typing /verify.",

        # -- webhook_handler.py translations for dispatch section
        "dispatch_payment_confirmed": "✅ <b>Payment Confirmed!</b>",
        "dispatch_seeker_body": "Your transaction for <b>{asset_title}</b> (Deal ID: <code>{deal_id}</code>) is now "
                                "complete.",
        "dispatch_match_finalized": "✅ <b>Match Finalized!</b>",
        "dispatch_provider_body": "An asset seeker has settled the platform commission for your listing: <b>{"
                                  "asset_title}</b> (Deal ID: <code>{deal_id}</code>).",
        "dispatch_category": "📂 <b>Category:</b> <code>{vertical_name}</code>",
        "dispatch_transaction": "🏷️ <b>Transaction:</b> <code>{transaction_type}</code>\n",
        "dispatch_asset_details": "🚙 <b>Asset Details:</b> <code>{mmy}</code>",
        "dispatch_location": "📍 <b>Location:</b> <code>{location}</code>",
        "dispatch_provider_phone": "📞 <b>Provider Phone:</b> <code>{phone}</code>",
        "dispatch_client_phone": "📞 <b>Client Phone:</b> <code>{phone}</code>",
        "not_specified": "Not specified",
        "person_unit": "Person",
        "people_unit": "People",

        "dispatch_route": "📍 <b>Route:</b> <code>{origin} ➡️ {destination}</code>\n",
        "dispatch_cargo_weight": "⚖️ <b>Cargo Weight:</b> <code>{weight} kg</b>\n",
        "dispatch_plate_number": "🚙 <b>Plate Number:</b> <code>{plate}</code>\n",
        "dispatch_party_size": "👥 <b>Party Size:</b> <code>{party}</code>\n",
        "lbl_not_specified": "Not specified",
        "dispatch_seeker_footer": "Directly contact the provider to arrange delivery. Thank you for using Smart Match "
                                  "Africa!",
        "dispatch_provider_footer": "Expect their call shortly, or contact them directly to get started. Thank you "
                                    "for using Smart Match Africa!",

        # Missing Keys Added
        "LABEL_LOGISTICS_PROFILE": "Logistics Profile",
        "LABEL_ESTIMATED_FARE": "Estimated Fare",
        "PROMPT_ENTER_OFFER_AMOUNT": "Please enter your custom offer amount:"
    },

    "am": {
        # =====================================
        # --- SYSTEM & ERRORS (sys_) ---
        # =====================================
        "sys_error_generic": "⚠️ የተሳሳተ ነገር አጋጥሟል። እባክዎ እንደገና ይሞክሩ ወይም ድጋፍ ሰጪን ያነጋግሩ።",
        "sys_invalid_number": "⚠️ የተሳሳተ ግብዓት። እባክዎ ቁጥሮችን ብቻ ያስገቡ (ለምሳሌ 1500)፡",
        "sys_invalid_text": "⚠️ የተሳሳተ ግብዓት። እባክዎ ትክክለኛ ጽሑፍ ያስገቡ።",
        "sys_cancelled": "🚫 ትዕዛዙ ተቋርጧል። ወደ ዋናው ማውጫ እየተመለሱ ነው።",
        "sys_loading": "⏳ ጥያቄዎን እያስተናገድን ነው...",
        "sys_photo_prompt": "📸 እባክዎ ጥራት ያለው ፎቶ ይላኩ፣ ወይም 'ዘልል' ን ይጫኑ።",

        # ========================================
        # --- COMMON BUTTONS (btn_) ---
        # ========================================
        "btn_yes": "አዎ (Yes)",
        "btn_no": "አይ (No)",
        "btn_cancel": "❌ ሰርዝ (Cancel)",
        "btn_confirm": "✅ አረጋግጥ (Confirm)",
        "btn_back": "⬅️ ተመለስ (Back)",

        # =====================================================================
        # MAIN MENU & WELCOME MESSAGE
        # =====================================================================
        "welcome_back": (
            "⚡ *እንኳን ወደ ስማርት ማች አፍሪካ በደህና መጡ!* ⚡\n\n"
            "> *የእርስዎ ቅጽበታዊ የንብረት እና የትራንስፖርት ገበያ ቦታ*\n\n"
            "የእኛ ቋሚ የገበያ ዝርዝር ማውጫ ከታች በኩል ተቆልፏል።\n\n"
            "*ዋና ዋና አገልግሎቶች:*\n"
            "• 🏠 *መኖሪያና ንግድ ቤት:* ቤት ለማውጣት ወይም ለመፈለግ\n"
            "• 🚘 *ተሽከርካሪዎች:* የመኪና አቅርቦትና ፍላጎት ለማገናኘት\n"
            "• 🚜 *ማሽነሪዎች:* የከባድ እና የእርሻ መሣሪያዎች\n"
            "• 📦 *ሎጂስቲክስ:* የጭነት ማጓጓዣ እና የከተማ ጉዞ\n\n"
            "👇 *ለመጀመር ከታች ካሉት አማራጮች አንዱን ይምረጡ:*"
        ),
        "menu_unrecognized": (
            "⚠️ *ያልታወቀ ትዕዛዝ*\n\n"
            "እባክዎ ከታች ባለው የዝርዝር ማውጫ ላይ ከሚገኙት አማራጮች አንዱን ይጠቀሙ።"
        ),
        # ----------------------------------------------------
        # MAIN MENU BUTTON LABELS (2x4 Grid Matrix)
        # ----------------------------------------------------
        "btn_list_house": "🏠 ቤት ለመመዝገብ",
        "btn_find_house": "🔍 ቤት ለመፈለግ",
        "btn_list_vehicle": "🚘 ተሽከርካሪ ለመመዝገብ",
        "btn_find_vehicle": "🔎 ተሽከርካሪ ለመፈለግ",
        "btn_list_equipment": "🚜 መሣሪያ ለመመዝገብ",
        "btn_find_equipment": "🏗️ መሣሪያ ለመፈለግ",
        "btn_freight": "📦 ጭነት",
        "btn_ride": "🚗 ጉዞ",

        # ----------------------------------------------------
        # MENU ENTRY PROMPTS
        # ----------------------------------------------------
        "menu_freight": "🚛 **የጭነት ማጓጓዣ (Freight Logistics)**\n\nዕቃ ማጓጓዝ ይፈልጋሉ ወይስ የጭነት መኪና አገልግሎት መስጠት?",
        "menu_ride": "🚕 **የትራንስፖርት አገልግሎት (Ride-Sharing)**\n\nትራንስፖርት እየፈለጉ ነው ወይስ አገልግሎት መስጠት ይፈልጋሉ?",
        "menu_list_house": "🏠 **ንብረትዎን ያስመዝግቡ**\n\nንብረትዎን ማከራየት ወይስ መሸጥ ይፈልጋሉ?",
        "menu_find_house": "🔍 **ንብረት ይፈልጉ**\n\nንብረት መከራየት ወይስ መግዛት ይፈልጋሉ?",
        "menu_list_vehicle": "🚗 **ተሽከርካሪ ያስመዝግቡ**\n\nተሽከርካሪዎን መሸጥ ወይስ ማከራየት ይፈልጋሉ?",
        "menu_find_vehicle": "🔍 **ተሽከርካሪ ይፈልጉ**\n\nተሽከርካሪ መግዛት ወይስ መከራየት ይፈልጋሉ?",

        "msg_choose_language_prompt": "🌐 *እንኳን ወደ ስማርት ማች አፍሪካ በደህና መጡ!*\n\nእባክዎ የሚፈልጉትን ቋንቋ ይምረጡ:",
        "msg_choose_language_short": "🌐 ቋንቋ ይምረጡ / Choose Language / Afaan Filadhu:",
        "msg_lang_set_success": "✅ ቋንቋዎ ወደ አማርኛ ተቀይሯል።",

        # ================================================
        # --- EQUIPMENT RENTAL (equip_) ---
        # ================================================
        # --- 1. MAIN MENU & CATEGORIES ---
        "menu_list_equip": "🏗️ **መሳሪያ ለማከራየት**\n\nየማሽነሪዎን ወይም የመሳሪያዎን ዋና ምድብ ይምረጡ፡",
        "menu_find_equip": "🔍 **መሳሪያ ለመከራየት**\n\nየሚፈልጉትን የመሳሪያ የስራ ዘርፍ ይምረጡ፡",
        "btn_cat_construction": "🚜 ኮንስትራክሽን እና ቁፋሮ",
        "btn_cat_farming": "🌾 ግብርና እና መስኖ",
        "btn_cat_generators": "⚡ ጄኔሬተሮች እና ሃይል",
        "btn_cat_eventtech": "🎪 ንግድ እና ዝግጅቶች",

        # --- 2. DATA INTAKE PIPELINE (LISTING & SOURCING) ---
        "prompt_equip_profile": "📝 **የመሳሪያው መገለጫ፡ {category}**\n\nእባክዎ የመሳሪያ አይነት ያስገቡ (ለምሳሌ፦ ሃይድሮሊክ ኤክስካቫተር ፣ የውሃ "
                                "ማስወገጃ ፖምፕ ፣ ስካፎልዲንግ)፡",
        "prompt_sourcing_req": "📋 **የመሳሪያ ፍላጎት፡ {category}**\n\nምን አይነት መሳሪያ/ማሽነሪ መከራየት ይፈልጋሉ?",
        "err_name_too_short": "⚠️ የመሳሪያው ስም በጣም አጭር ነው። እባክዎ ግልጽ የሆነ መግለጫ ያስገቡ።",

        "prompt_capacity_list": "⚙️ **አቅም እና የስራ መጠን**\n\nየ *{equip_type}* አቅም ወይም የስራ መጠን ስንት ነው? (ለምሳሌ፦ 20 ቶን፣ 500 "
                                "kVA፣ 150 L/s)፡",
        "prompt_capacity_find": "⚙️ **የሚፈለግ ዝርዝር**\n\nለ *{equip_type}* የሚፈለገው ዝቅተኛ አቅም ስንት ነው? (ምንም ካልመረጡ 'Any' ብለው "
                                "ይጻፉ)፡",

        "prompt_brand_list": "🏷️ **ብራንድ እና ቴክኒካዊ ባህሪያት**\n\nየአምራቹን ስም እና ዋና ዋና የቴክኒክ ባህሪያትን በሰረዝ ለይተው ያስገቡ (ለምሳሌ፦ "
                             "Caterpillar - GPS Guided)፡",
        "prompt_brand_find": "🏷️ **ተመራጭ ብራንድ**\n\nየሚመርጡት አምራች ወይም የተለየ ቴክኒካዊ ባህሪ አለ? (ለምሳሌ፦ Caterpillar - GPS "
                             "Guided፣ ምርጫ ከሌለዎት 'None' ብለው ይጻፉ)፡",

        "prompt_location_list": "📍 **የመሳሪያው መገኛ**\n\nእባክዎ የመሳሪያውን አሁን ያለበትን ቦታ ቴሌግራም ላይ ያለውን ሎኬሽን (📎) በመጠቀም ይላኩ፣ "
                                "ወይም አድራሻውን ይጻፉ፡",
        "prompt_location_find": "📍 **የስራው ቦታ**\n\nይህ መሳሪያ የት ነው የሚሰራው? የፕሮጀክቱን ቦታ ቴሌግራም ላይ ያለውን ሎኬሽን (📎) በመጠቀም ይላኩ "
                                "ወይም አድራሻውን ይጻፉ፡",
        "gps_coords_provided": "🌐 የጂፒኤስ (GPS) መጋጠሚያዎች ተመዝግበዋል",

        # --- 3. COMMERCIALS & CONDITION ---
        "prompt_rate_list": "💰 **የሰዓት ክፍያ ተመን (በኢትዮጵያ ብር/በአሜሪካ ዶላር)**\n\nእባክዎ የሚጠይቁትን የሰዓት ኪራይ ተመን ምንዛሬውን በግልጽ በመለየት "
                            "በኢትዮጵያ ብር (ETB) ወይም በአሜሪካ ዶላር (USD) ያስገ፦",
        "prompt_rate_find": "💰 የበጀት ጣሪያ (በኢትዮጵያ ብር/በአሜሪካ ዶላር)\n\nከፍተኛውን ተቀባይነት ያለውን የሰዓት ክፍያ ምንዛሬውን በግልጽ በመለየት "
                            "በኢትዮጵያ ብር (ETB) ወይም በአሜሪካ ዶላር (USD) ያስገቡ",
        "err_invalid_rate": "⚠️ የተሳሳተ ግቤት። እባክዎ ለዋጋው ትክክለኛ ቁጥር ያስገቡ።",
        "err_invalid_rate_system": "❌ የዋጋ ተመኑን በማረጋገጥ ላይ የስርዓት ስህተት አጋጥሟል። እባክዎ እንደገና ይሞክሩ።",

        "prompt_condition_list": "🛠️ **የመሳሪያው ሁኔታ**\n\nየመሳሪያውን የአሁኑን ሜካኒካዊ ሁኔታ ይምረጡ፡",
        "prompt_condition_find": "🛠️ **አነስተኛ ተቀባይነት ያለው ሁኔታ**\n\nለፕሮጀክትዎ የሚያስፈልገውን አነስተኛ ሜካኒካዊ ሁኔታ ይምረጡ፡",
        "btn_cond_exc": "🌟 እጅግ በጣም ጥሩ / እንደ አዲስ",
        "btn_cond_good": "✅ ጥሩ / ለስራ ዝግጁ",
        "btn_cond_fair": "⚠️ መካከለኛ / ይሰራል",

        # --- 4. MEDIA & CONTACT ---
        "prompt_photo_upload": "📸 **የመሳሪያው መረጃ**\n\nእባክዎ በስራ ቦታው ያለውን የመሳሪያውን ጥራት ያለው ፎቶ (📎) ይላኩ። ያለ ፎቶ ለመቀጠል "
                               "'Skip' ብለው ይጻፉ።",
        "msg_compressing_image": "⏳ ምስሉን በማስተካከል እና ወደ ደህንነቱ የተጠበቀ ማከማቻ በመጫን ላይ...",
        "err_upload_timeout": "⚠️ ምስል መጫን ጊዜ አልፎበታል። በጽሁፍ መረጃ ብቻ በመቀጠል ላይ።",

        "prompt_contact_info": "📞 **የስልክ ቁጥር**\n\nእባክዎ የሳይት ማናጀሩን ወይም የመሳሪያውን አስተባባሪ ስልክ ቁጥር ያስገቡ፡",
        "msg_finalizing_packet": "⚙️ መረጃዎችን በማጠናቀር ላይ...",
        "msg_asset_live": "✅ **መረጃው ገብቷል!**\n\nየእርስዎ {category} መሳሪያ (*{equip_type}*) አሁን በስማርት ማች ላይ ገብቷል፣ ኮንትራክተሮችም "
                          "ሊያገኙት ይችላሉ።",
        "MSG_TOP_EQUIP_MATCHES": "🎯 **{count} ምርጥ የዕቃዎች ግጥሚያዎች ተገኙ!**\n\nእባክዎ ለመገምገም እና ቅናሽ ለመላክ ከዚህ በታች ያለውን ንብረት "
                                 "ይምረጡ:",

        # --- 5. MATCHING & DEALS ---
        "msg_scanning_nodes": "📡 ከእርስዎ መስፈርት ጋር የሚዛመዱ ማሽነሪዎችን በመፈለግ ላይ...",
        "msg_machinery_secured": "🎯 **ተስማሚ መሳሪያ ተገኝቷል! (የመዛመድ ውጤት፡ {score}%)**\n\n**መሳሪያ፡** {brand} {"
                                 "equip_type}\n**ቦታ፡** {location}\n**ዋጋ፡** {rate} ብር\n\nይህን መሳሪያ ማስከበር እና የኮሚሽን ሂደቱን "
                                 "መጀመር ይፈልጋሉ?",
        "btn_rent_secure": "🔒 መሳሪያውን አስይዝ እና ክፈል",
        "btn_decline": "❌ አልፈልግም",
        "err_system_anomaly": "❌ የውስጥ ስርዓት ችግር አጋጥሟል። እባክዎ ሂደቱን እንደገና ይጀምሩ።",
        "err_request_not_found": "⚠️ የተመረጠው ጥያቄ ከአሁን በኋላ ንቁ አይደለም።",

        # --- 6. BILLING & ESCROW ---
        "msg_initiating_gateway": "💳 የክፍያ ስርዓትን በመጀመር ላይ...",
        "msg_asset_link_secured": "🧾 **ደረሰኝ ተዘጋጅቷል**\n\nየመሳሪያውን መርሐግብር ለማስያዝ እና የአቅራቢውን አድራሻ ለማግኘት እባክዎ ክፍያውን ያጠናቅቁ።",
        "btn_open_portal": "🌐 የክፍያ ገጹን ክፈት",
        "btn_verify_payment": "🔄 ክፍያውን አረጋግጥ",
        "err_gateway_request": "❌ ከክፍያ ስርዓቱ ጋር መገናኘት አልተቻለም። እባክዎ ቆየት ብለው ይሞክሩ።",
        "msg_escrow_cleared": "✅ **ክፍያው ተረጋግጧል እና መሳሪያው ተይዟል!**\n\nግብይቱ ተጠናቋል። \n\n**የአቅራቢው አድራሻ፡** {"
                              "provider_contact}\n\nእባክዎ ስራ ለመጀመር በቀጥታ ይገናኙ።",
        "msg_payment_pending": "⏳ ክፍያው ገና አልተጠናቀቀም። እባክዎ በክፍያ ገጹ ላይ ግብይቱን ያጠናቅቁ እና እንደገና አረጋግጥ የሚለውን ይጫኑ።",

        # --- 7. NEGOTIATION / OFFERS ---
        "equip_offer_prompt": "🤝 **ዋጋ አቅርብ**\n\nእባክዎ ለዚህ መሳሪያ መክፈል የሚፈልጉትን የመነሻ ዋጋ (በብር) ያስገቡ፡",
        "btn_cancel_offer": "🚫 የዋጋ ማቅረቢያውን ሰርዝ",
        "err_listing_unavailable": "❌ ይህ ማሽነሪ አሁን ላይ አይገኝም።",
        "err_offer_own_listing": "⚠️ በራስዎ መሳሪያ ላይ ዋጋ ማቅረብ አይችሉም።",
        "err_offer_failed": "❌ የድርድር ሂደቱን መጀመር አልተቻለም። እባክዎ እንደገና ይሞክሩ።",
        "msg_process_cancelled": "🛑 የመሳሪያ ኪራይ ሂደቱ ተቋርጧል።",

        # Match Selection & Asset Card
        "msg_no_matches_found": "⚠️ በአሁኑ ጊዜ ምንም ንቁ ግጥሚያዎች አልተገኙም። ወደ ዋናው ምናሌ በመመለስ ላይ...",
        "err_asset_not_found": "⚠️ የተመረጠው መሣሪያ አሁን አገልግሎት ላይ አይገኝም ወይም ዝግጁ አይደለም።",
        "lbl_equipment_selected": "🚜 <b>የተመረጠው መሣሪያ፦</b> {asset_title}",
        "lbl_rental_rate": "💰 <b>የኪራይ ዋጋ፦</b> <code>{price:,.2f} ETB</code>",
        "lbl_capacity_output": "⚡ <b>አቅም/ምርት፦</b> <code>{capacity}</code>",
        "prompt_select_option": "የፕሮፖዛል (የሃሳብ ማቅረቢያ) ዋጋዎን ለማስገባት ከሚከተሉት አማራጮች ውስጥ አንዱን ይምረጡ፦",

        # Custom Offer Prompt & Processing
        "prompt_custom_offer": (
            "💬 <b>የሚያቀርቡትን ዋጋ ያስገቡ</b>\n\n"
            "🚜 መሣሪያ፦ <i>{model_name}</i>\n"
            "📍 ቦታ፦ <b>{location_text}</b>\n"
            "💰 የተጠየቀ ዋጋ፦ <code>{asking_price:,.2f} ETB</code>\n\n"
            "እባክዎ የሚያቀርቡትን ዋጋ በኢትዮጵያ ብር በቁጥር ብቻ ያስገቡ (ለምሳሌ፦ <code>5000</code>)፦"
        ),

        # Transmission & Deal Locking Status
        "msg_offer_transmitted": (
            "✅ <b>የዋጋ ጥያቄዎ ተላከ!</b>\n\n"
            "ለ <i>{safe_asset_title}</i> ያቀረቡት <b>{formatted_price} ETB</b> ዋጋ ለባለቤቱ ተልኳል።"
        ),
        "msg_equipment_selected": "🚜 <b>የተመረጠ መሣሪያ፡</b>",
        "msg_property_use": "🛠️ <b>የመሳሪያ ዓይነት:</b>",
        "msg_rental_rate": "💰 <b>የኪራይ ዋጋ፡</b>",
        "msg_capacity_output": "⚡ <b>አቅም/ውጤት፡</b>",
        "msg_select_offer_option_equip": "ለባለቤቱ የዋጋ ማቅረቢያ ለመላክ ከዚህ በታች ካሉት አማራጮች አንዱን ይምረጡ፡",
        "msg_location": "📍 <b>ቦታ፡</b>}",
        "err_self_offer": "⚠️ በራስዎ ንብረት ላይ ዋጋ ማቅረብ አይችሉም።",
        "msg_select_top_match": "🚜 <b>ተስማሚ የመሣሪያ ምርጫዎች:</b>\n\nእባክ ከዚህ በታች ካሉት አማራጮች አንዱን ይምረጡ:",
        "msg_select_transport_match": "🚚 <b>ትክክለኛ የተዛመዱ የመጓጓዣ አማራጮች፡</b>\n\nእባክዎ ከታች ካሉት አማራጮች አንዱን ይምረጡ፡",
        "msg_select_housing_match": "🏠 <b>ትክክለኛ የተዛመዱ የቤት አማራጮች፡</b>\n\nእባክዎ ከታች ካሉት አማራጮች አንዱን ይምረጡ፡",
        "BTN_CANCEL": "❌ ፍለጋ ሰርዝ",
        "msg_scanning_seekers": "ተዛማጅ ፈላጊዎችን በመቃኘት ላይ...",
        "BTN_SKIP": "ዝለል",
        "msg_top_seekers_found": "ከፍተኛ ተዛማጅ ፈላጊዎች ተገኝተዋል!",
        "msg_seeker_request_selected_equip": "የፈላጊ ጥያቄ በተሳካ ሁኔታ ተመርጧል። ማዛመድ በመከናወን ላይ ነው...",
        "msg_no_seeker_matches": "በዚህ ሰዓት ተዛማጅ የፈላጊ ጥያቄ አልተገኘም፣ አዲስ ጥያቄ ሲደርሰን ወዲያውኑ እናሳውቆታለን።",
        "dispatch_reverse_seeker_body": (
            "ለጥያቄዎ የፕላትፎርም ኮሚሽን በተሳካ ሁኔታ ተከፍሏል፡ <b>{asset_title}</b> "
            "(የውል መለያ፡ <code>{deal_id}</code>)።\nየተስማሙበት ዋጋ፡ <b>{price} {currency}</b>"
        ),
        "dispatch_reverse_provider_body": (
            "የሀብት ፈላጊ ለተዛመደው ጥያቄዎ ኮሚሽን ከፍሏል፡ <b>{asset_title}</b> "
            "(የውል መለያ፡ <code>{deal_id}</code>)።\nየተስማሙበት ዋጋ፡ <b>{price} {currency}</b>"
        ),
        "dispatch_reverse_match_seeker_header": "✅ <b>ግጥሚያው ተጠናቀቀ! (የተገላቢጦሽ ግጥሚያ)</b>",
        "dispatch_reverse_match_provider_header": "✅ <b>ግጥሚያው ተጠናቀቀ! (የተገላቢጦሽ ግጥሚያ)</b>",

        # ===============================================
        # --- HOUSING (house_) ---
        # ===============================================
        "msg_return_hub": "ወደ ዋናው ማውጫ በመመለስ ላይ...",
        "msg_return_root": "ተገቢውን ምላሽ እየጠበቅን ነው፤ እባክዎን በትዕግስት ይቆዩ፣ ወይም ወደ ሌላ ተግባር ለመቀየር ዋናውን ሜኑ ይጠቀሙ ወይም /start ብለው "
                           "ይላኩ።",
        "btn_return_main": "🔙 ዋና ማውጫ",
        "msg_list_house_prompt": "🏡 **ንብረትዎን ያስመዝግቡ**\n\nንብረትዎን ማከራየት ወይስ መሸጥ ይፈልጋሉ?",
        "btn_rent_out": "🔑 ማከራየት",
        "btn_sell": "💰 መሸጥ",
        "msg_find_house_prompt": "🔍 **ንብረት ይፈልጉ**\n\nንብረት መከራየት ወይስ መግዛት ይፈልጋሉ?",
        "btn_rent_in": "🔑 መከራየት",
        "btn_buy": "💰 መግዛት",
        "msg_property_sector": "🏢 **የንብረት ዓይነት**\n\nይህ የመኖሪያ ወይስ የንግድ ቦታ ነው?",
        "btn_residential": "🏠 የመኖሪያ ቤት",
        "btn_commercial": "🏢 የንግድ ቦታ",
        "btn_residential_plot": "የመኖሪያ መሬት",
        "btn_commercial_plot": "የንግድ መሬት",
        "msg_location_list": "📍 **አድራሻ**\n\nእባክዎ የንብረቱን መገኛ አካባቢ በካርታ (📎) ያጋሩ ወይም ከተማውን፣ ክፍለ ከተማውን/ወረዳ/ቀበሌውን እና "
                             "ሰፈሩን ይፃፉ።",
        "msg_location_find": "📍 **የሚፈልጉት አድራሻ**\n\nየት አካባቢ ነው የሚፈልጉት? እባክዎ አካባቢውን በካርታ ያጋሩ  ወይም ከተማውን፣ ክፍለ "
                             "ከተማውን/ወረዳ/ቀበሌውን እና ሰፈሩን ይፃፉ።",
        "err_location_short": "⚠️ ያስገቡት አድራሻ አጭር ነው። እባክዎ ትክክለኛ እና ግልጽ አካባቢ ያስገቡ።",
        "msg_price_list": "💵 **ዋጋ**\n\nእባክዎ የሚጠይቁትን ዋጋ ከነገንዘብ ዓይነቱ ይግለጹ (ምሳሌ፦ ለወርሃዊ ኪራይ 25,000 ETB ወይም 500 USD፣ ወይም "
                          "ለሽያጭ 1,500,000 ETB ወይም 30,000 USD)።",
        "msg_budget_find": "💵 **ከፍተኛ በጀት**\n\nእባክዎ ከፍተኛውን የበጀት መጠንዎትን ከነገንዘብ ዓይነቱ ይግለጹ (ምሳሌ፦ ለወርሃዊ ኪራይ 25,"
                           "000 ETB ወይም 500 USD፣ ወይም ለሽያጭ 1,500,000 ETB ወይም 20,500 USD)።",
        "err_invalid_number": "⚠️ የተሳሳተ ቁጥር። እባክዎ ትክክለኛ ቁጥር ብቻ ያስገቡ (ለምሳሌ 25000)።",
        "msg_beds_list": "🛏️ **መኝታ ቤቶች**\n\nንብረቱ ስንት መኝታ ቤቶች አሉት? ትክክለኛ ቁጥር ያስገቡ (ለምሳሌ 3)።",
        "msg_beds_find": "🛏️ **መኝታ ቤቶች**\n\nቢያንስ ስንት መኝታ ቤቶች ይፈልጋሉ? ትክክለኛ ቁጥር ያስገቡ (ለምሳሌ 2)።",
        "msg_sqm_list": "📏 **ስፋት (በካሬ ሜትር)**\n\nየቦታው ስፋት ስንት ካሬ ሜትር ነው? ትክክለኛ ቁጥር ያስገቡ።",
        "msg_sqm_find": "📏 **ስፋት (በካሬ ሜትር)**\n\nየሚፈልጉት ቦታ ቢያንስ ስንት ካሬ ሜትር መሆን አለበት?",
        "err_invalid_whole_number": "⚠️ የተሳሳተ መረጃ። እባክዎ ትክክለኛ ሙሉ ቁጥር ያስገቡ።",
        "keyword_none": "የለም",
        "msg_amenities_list": "✨ **ተጨማሪ አገልግሎቶች**\n\nዋና ዋና አገልግሎቶችን በንዑስ ሰረዝ እየለዩ ይፃፉ (ምሳሌ፦ የመኪና ማቆሚያ፣ ዋይፋይ፣ ጄኔሬተር፣ "
                              "ተጠባባቂ ውሃ)። ምንም የተለየ መገልገያ ከሌለ 'None' ብለው ይፃፉ።",
        "msg_amenities_find": "✨ **የሚፈልጓቸው አገልግሎቶች**\n\nበግድ ሊኖሩ የሚገባቸውን ነገሮች በንዑስ ሰረዝ እየለዩ ይፃፉ (ለምሳሌ፦ የመኪና ማቆሚያ፣ "
                              "ዋይፋይ፣ ጄኔሬተር፣ ተጠባባቂ ውሃ)። ግዴታ ካልሆነ 'የለም' ይበሉ።",
        "keyword_skip": "ዘልል",
        "msg_photo_prompt": "📸 **የንብረቱ ፎቶ**\n\nእባክዎ የንብረቱን ጥራት ያለው ፎቶ ይላኩ። ፎቶ ከሌለዎት 'ዘልል' ብለው ይፃፉ።",
        "msg_uploading_b2": "⏳ ፎቶውን ወደ ደህንነቱ የተጠበቀ ማከማቻ በመጫን ላይ...",
        "msg_photo_success": "✅ ፎቶው በተሳካ ሁኔታ ተጭኗል!",
        "msg_photo_timeout": "⚠️ ፎቶውን መጫን አልተቻለም። ያለ ፎቶ እንቀጥላለን።",
        "err_photo_invalid": "⚠️ የተሳሳተ መረጃ። እባክዎ ፎቶ ይላኩ ወይም 'ዘልል' ብለው ይፃፉ።",
        "msg_bulk_qty": "📦 እባክዎ የሚገኘውን  አጠቃላይ ብዛት ያስገቡ:",
        "msg_contact_list": "📞 **የመገናኛ መረጃ**\n\nደንበኞች እንዲያገኙዎ እባክዎ ስልክ ቁጥርዎን ያስገቡ (ለምሳሌ 0911234567)።",
        "msg_contact_find": "📞 **የመገናኛ መረጃ**\n\nተስማሚ ንብረት ሲገኝ አቅራቢዎች እንዲያገኙዎ ስልክ ቁጥርዎን ያስገቡ (ለምሳሌ 0911234567)።",
        "err_invalid_phone": "⚠️ የተሳሳተ ስልክ ቁጥር። እባክዎ ትክክለኛ ባለ 9 ወይም 10 ዲጂት ስልክ ቁጥር ያስገቡ።",
        "msg_processing_asset": "⚙️ መረጃዎን በማቀነባበር እና ከሚመጥኑ አማራጮች ጋር በማገናኘት ላይ...",
        "msg_listing_success": "🎉 **ተመዝግቧል!**\n\nንብረትዎ በተሳካ ሁኔታ ተመዝግቧል። ተስማሚ ደንበኞች ሲገኙ እናሳውቆታለን።",
        "msg_request_success": "📡 **ጥያቄዎ ተመዝግቧል!**\n\nፍላጎትዎ ተመዝግቧል። ስርዓቱን በመቃኘት ምርጥ አማራጮችን እናሳውቆታለን።",
        "err_system_db": "❌ መረጃዎን በማስቀመጥ ላይ የውስጥ ችግር አጋጥሟል። እባክዎ ትንሽ ቆይተው እንደገና ይሞክሩ።",
        "lbl_area_size": "የቦታ ስፋት:",
        "lbl_bedrooms": " የመኝታ ክፍሎች ብዛት: ",
        "lbl_property_selected": "የተመረጠ ንብረት",
        "lbl_asking_price": "💰 የተጠየቀ ዋጋ:",
        "lbl_select_option": "ለባለቤቱ የዋጋ ማቅረቢያዎን ለመላክ ከታች ያለውን አማራጭ ይምረጡ:",
        "BTN_ACCEPT_ASKING": "የመጠየቂያ ዋጋውን ተቀበል",
        "housing_offer_title": "🔔 አዲስ የቀረበ የቤት ዋጋ!",
        "objective": "ዓላማ",
        "asset": "ንብረት",
        "rental": "ኪራይ",
        "sale": "ሽያጭ",
        "residential": "የመኖሪያ",
        "commercial": "የንግድ",
        "offer_price": "የቀረበ ዋጋ",
        "match_btn_format": "🏠 #{idx}: {prop_type} በ {location} ({price} ብር - {score}%)",

        # Housing & Match Displays
        "msg_scanning_housing_nodes": "🔍 ያሉትን ይዞታዎች/ቤቶች በመፈለግ ላይ...",
        "MSG_TOP_HOUSING_MATCHES": "🎯 <b>ከፍላጎትዎ ጋር የሚጣጣሙ {count} ምርጥ ንብረቶች ተገኝተዋል!</b>\n\nእባክዎን ከታች ከተዘረዘሩት ውስጥ መርጠው"
                                   " የዋጋ ማቅረቢያ ይላኩ፡",
        "notice_new_offer_received": "🔔 <b>አዲስ የ{vertical_type} የዋጋ ማቅረቢያ!</b>\n\n🏠 <b>ንብረት፡</b> {asset_title}\n💰 "
                                     "<b>የቀረበ ዋጋ፡</b> <code>{price} ብር</code>",

        # Buttons & Options
        "BTN_SEND_OFFER": "🤝 የራስዎን ዋጋ ያቅርቡ",
        "btn_accept_asking": "✅ የተጠየቀውን/የቀረበውን ዋጋ ተቀበሉ ({price} ብር)",

        "lbl_objective": "ዓላማ:",
        "btn_accept_price": "✅ ዋጋውን ተቀበል",

        # Prompts & Status Messages
        "msg_seeker_request_selected_housing": "👤 <b>የፈላጊ ጥያቄ ተመርጧል፡</b>",
        "prompt_enter_offer": (
            "💬 <b>የሚያቀርቡትን ዋጋ ያስገቡ</b>\n\n"
            "ንብረት፦ <i>{asset_title}</i>\n"
            "የተጠየቀ ዋጋ፦ <code>{price} ብር</code>\n\n"
            "እባክዎን የሚያቀርቡትን ዋጋ በቁጥር ያስገቡ (ምሳሌ፦ <code>22000</code>)፦"
        ),
        "err_deal_blocked_conflict": "⚠️ **ድርድሩ ታግዷል**\n\nቆይታዎ አብቅቷል ወይም ሌላ ተጠቃሚ እየተሳተፈ ነው። እባክዎ ገፁን ያድሱ።",
        "err_deal_expired": "⚠️ ይህ ስምምነት ጊዜው አልፎበታል ወይም አሁን ንቁ አይደለም።",
        "err_target_unavailable": "⚠️ ቅናሹን ማድረስ አልተቻለም። ተቀባዩ አይገኝም።",
        "err_unexpected": "⚠️ ያልተጠበቀ ስህተት ተከስቷል።",
        "notice_counter_received": "🔄 **የድርድር ማሻሻያ**\n\n📂 **አይነት:** {vertical_type}\n🏠 **ንብረት:** {asset_title}\n👤 "
                                   "**ከ:** {source_actor}\n💰 **የተሻሻለ ዋጋ:** **{price} ETB**",
        "role_provider": "አቅራቢ",
        "role_seeker": "ፈላጊ",
        "lbl_default_asset": "የንብረት ዝርዝር",
        "msg_match_cancelled": "❌ የንብረት ፍለጋው ተሰርዟል። ወደ ዋናው ማውጫ በመመለስ ላይ...",

        "alert_search_cancelled": "ፍለጋው ተሰርዟል።",
        "err_verify_provider": "⚠️ የንብረት አቅራቢውን ማረጋገጥ አልተቻለም። እባክዎ እንደገና ይፈልጉ።",
        "msg_transmitting_offer": "🔄 የዋጋ ማቅረቢያ ለአቅራቢው በደህንነት እየተላለፈ ነው...",
        "msg_offer_sent": "✅ <b>የዋጋ ማቅረቢያዎ በተሳካ ሁኔታ ተልኳል!</b>\n\nለ <b>{asset_title}</b> ያቀረቡት የ{transaction_type} ዋጋ "
                          "<b>{price} ብር</b> ለአቅራቢው ተልኳል። ሲመልሱ ማሳወቂያ ይደርሶታል።",
        "err_deal_init_failed": "⚠️ የቤት ድርድር መጀመር አልተቻለም። እባክዎ ቆይተው እንደገና ይሞክሩ።",

        # Errors, Blockers & Warnings
        "err_internal_pipeline": "⚠️ መረጃውን በማቀናበር ላይ የውስጥ ስህተት አጋጥሟል። እባክዎ በኋላ እንደገና ይሞክሩ።",

        "err_property_not_found": "⚠️ የተመረጠው ንብረት በአሁኑ ጊዜ አይገኝም።",
        "err_pending_negotiation": (
            "🚫 <b>ድርድር አልተፈቀደም</b>\n\n"
            "ይህ ንብረት በአሁኑ ጊዜ ከሌላ ተጠቃሚ ጋር ንቁ ድርድር ላይ ነው። እባክዎን ቆየት ብለው ይሞክሩ።"
        ),
        "msg_request_broadcasted": "📢 ተስማሚ ንብረት በአቅራቢያ አልተገኘም። ጥያቄዎ ለሀብት አቅራቢዎች ተሰራጭቷል። ንብረቱ ሲገኝ ማሳወቂያ ይደርስዎታል።",
        "btn_pay_chapa": "💳 የኮሚሽን ክፍያ በቻፓ ይክፈሉ",
        "msg_escrow_commission_fee": "🔐 **የአከራይ ወይም ሻጭ መረጃ**\n\nየተረጋገጠውን የአከራይ ስልክ ቁጥር እና ትክክለኛ መገኛ (GPS) ለማግኘት፣ "
                                     "እባክዎ የፕላትፎርም ኮሚሽን **{amount} ብር** ይክፈሉ።",
        "err_chapa_init": "❌ የቻፓ የክፍያ ስርዓትን ማስጀመር አልተቻለም። እባክዎ እንደገና ይሞክሩ።",
        "err_chapa_system": "❌ ከክፍያ ስርዓቱ ጋር በመገናኘት ላይ ከባድ ስህተት አጋጥሟል።",

        # =============================================
        # --- VEHICLES (veh_) ---
        # =============================================
        "LIST_VEHICLE": "🚗 መኪና ያውጡ",
        "FIND_VEHICLE": "🔍 መኪና ይፈልጉ",
        "vehicle.btn.return_main": "🏠 ወደ ዋና ማውጫ ተመለስ",
        "vehicle.btn.back": "⬅️ ወደኋላ",
        "vehicle.btn.cancel": "❌ ሰርዝ",
        "vehicle.btn.submit": "✅ ጥያቄውን ላክ",
        "vehicle.offer.prompt": "🤝 **ዋጋ ያቅርቡ**\n\nእባክዎ የመነሻ ዋጋዎን በብር ያስገቡ (ቁጥር ብቻ)።",
        "vehicle.btn.cancel_offer": "❌ ጥያቄውን ሰርዝ",
        "vehicle.error.invalid_number": "⚠️ የተሳሳተ መጠን። እባክዎ ቁጥር ብቻ ያስገቡ።",
        "vehicle.error.listing_unavailable": "⚠️ ይህ ተሽከርካሪ አሁን ላይ አይገኝም።",
        "vehicle.error.cannot_offer_own": "⚠️ በራስዎ ተሽከርካሪ ላይ ዋጋ ማቅረብ አይችሉም።",
        "vehicle.error.system_offline": "⚠️ የዋጋ ድርድር ስርዓቱ አሁን ላይ አይሰራም። እባክዎ ትንሽ ቆይተው ይሞክሩ።",
        "vehicle.offer.sent_success": "✅ የ **{initial_offer} ብር** ጥያቄዎ ለአቅራቢው ተልኳል። ምላሽ ሲሰጡ እናሳውቆታለን።",
        "vehicle.offer.sent_fail": "❌ ጥያቄዎን መላክ አልተቻለም። እባክዎ እንደገና ይሞክሩ።",

        "vehicle.list.welcome": "🚗 **ተሽከርካሪ ያስመዝግቡ**\n\nተሽከርካሪዎን መሸጥ ወይስ ማከራየት ይፈልጋሉ?",
        "vehicle.btn.sell_vehicle": "💰 መሸጥ",
        "vehicle.btn.rent_out": "🔑 ማከራየት",
        "vehicle.find.welcome": "🔍 **ተሽከርካሪ ይፈልጉ**\n\nተሽከርካሪ መግዛት ወይስ መከራየት ይፈልጋሉ?",
        "vehicle.btn.buy_vehicle": "💰 መግዛት",
        "vehicle.btn.rent_lease": "🔑 መከራየት",

        "vehicle.vtype.title": "⚡ **ስማርት ማች የተሽከርካሪ ዓይነት** ⚡",
        "vehicle.vtype.sedan": "🚗 አነስተኛ መኪና (Sedan)",
        "vehicle.vtype.suv": "🚙 የቤት አውቶሞቢል (SUV)",
        "vehicle.vtype.van": "🚐 ቫን (Van)",
        "vehicle.vtype.bus": "🚌 አውቶቡስ / ሚኒባስ",
        "vehicle.vtype.truck": "🚚 የጭነት መኪና (Truck)",
        "vehicle.vtype.moto": "🏍️ ሞተር ሳይክል (Moto)",
        "vehicle.list.vtype_prompt": "ሊዘርዝሩት (ሊያጋሩት) የሚፈልጉትን የተሽከርካሪ ዓይነት ይምረጡ፦",
        "vehicle.find.vtype_prompt": "ሊያገኙት (ሊከራዩ/ሊገዙ) የሚፈልጉትን የተሽከርካሪ ዓይነት ይምረጡ፦",

        "vehicle.list.make_model_prompt": "🏷️ **ስም ፣ ሞዴል እና የተመረተበት ዓ.ም**\n\nእባክዎ የተሽከርካሪውን ስም፣ ሞዴል እና የተመረተበትን ዓ.ም "
                                          "ይፃፉ (ለምሳሌ፦ ቶዮታ ዶልፊን ሚኒባስ 2LT 2000)።",
        "vehicle.find.make_model_prompt": "🏷️ **ስም ፣ ሞዴል እና የተመረተበት ዓ.ም**\n\nእባክዎ የሚፈልጉትን የተሽከርካሪ ስም፣ ሞዴል እና "
                                          "የተመረተበትን ዓ.ም ይፃፉ (ለምሳሌ፦ ቶዮታ ዶልፊን ሚኒባስ 2LT 2000)።",
        "vehicle.error.input_too_short": "⚠️ ያስገቡት መረጃ አጭር ነው። እባክዎ የተሽከርካሪውን ስም እና ሞዴል በግልጽ ይፃፉ።",

        "vehicle.list.condition_prompt": "⏳ **ሁኔታ / ያገለገለበት ጊዜ**\n\nተሽከርካሪው ለስንት ዓመታት አገልግሏል? አዲስ ከሆነ '0' ብለው ያስገቡ።",
        "vehicle.find.condition_prompt": "⏳ **ሁኔታ / ያገለገለበት ጊዜ**\n\nየሚፈልጉት ተሽከርካሪ ቢበዛ ለስንት ዓመታት ያገለገለ ቢሆን ይመረጣል? አዲስ "
                                         "ብቻ ከፈለጉ '0' ያስገቡ።",
        "vehicle.error.invalid_format_number": "⚠️ የተሳሳተ ቅርጸት። እባክዎ ትክክለኛ ዓመት በቁጥር ያስገቡ።",

        "vehicle.fuel.title": "⛽ **የነዳጅ ዓይነት**",
        "vehicle.fuel.benzine": "⛽ ቤንዚን",
        "vehicle.fuel.diesel": "🛢️ ዲዝል (ናፍጣ)",
        "vehicle.fuel.electric": "⚡ ኤሌክትሪክ",
        "vehicle.fuel.lng_ngv": "🌐 ኤል.ኤን.ጂ / የተፈጥሮ ጋዝ (NGV)",
        "vehicle.fuel.hybrid_petrol": "🔋 ቤንዚን-ኤሌክትሪክ ሃይብሪድ",
        "vehicle.fuel.hybrid_diesel": "🔋 ዲዝል-ኤሌክትሪክ ሃይብሪድ",
        "vehicle.fuel.hybrid_flex": "🌱 ፍሌክስ-ፉኤል (ኢታኖል) ሃይብሪድ",
        "vehicle.fuel.hybrid_gaseous": "🔥 ባለሁለት-ነዳጅ (ጋዝ-ኤሌክትሪክ) ሃይብሪድ",
        "vehicle.fuel.hybrid_hydrogen": "💧 ሃይድሮጅን ፉኤል ሴል ሃይብሪድ",
        "vehicle.list.fuel_prompt": "የተሽከርካሪውን የነዳጅ ዓይነት ይምረጡ፡",
        "vehicle.find.fuel_prompt": "የሚፈልጉትን የነዳጅ ዓይነት ይምረጡ፡",

        "vehicle.keyword.none": "የለም",
        "vehicle.list.features_prompt": "✨ **ተጨማሪ መግለጫዎች**\n\nተጨማሪ ነገሮችን በነጠላ ሰረዝ  እየለዩ ይፃፉ ((ለምሳሌ፦ የኤሲ አየር ማቀዝቀዣ፣ "
                                        "ማኑዋል ማርሽ፣ የጀርባ ካሜራ)። የተለየ ነገር ከሌለ 'የለም' ብለው ይፃፉ።",
        "vehicle.find.features_prompt": "✨ **የሚፈልጓቸው ተጨማሪ ነገሮች**\n\nበግድ ሊኖሩ የሚገባቸውን ነገሮች በነጠላ ሰረዝ እየለዩ ይፃፉ (ለምሳሌ፦ "
                                        "አውቶማቲክ ማርሽ፣ የኤሲ አየር ማቀዝቀዣ)። ግዴታ ካልሆነ 'የለም' ይበሉ።",

        "vehicle.list.location_prompt": "📍 **አድራሻ**\n\nተሽከርካሪው አሁን የት ነው ያለው? መገኛዎን ያጋሩ (📎) ወይም ሀገር፣ ከተማ/ሰፈር ያስገቡ።።",
        "vehicle.find.location_prompt": "📍 **የሚፈልጉበት አድራሻ**\n\nተሽከርካሪውን የት ቦታ ነው የሚፈልጉት? መገኛዎን (📎) ያጋሩ ወይም ሀገር፣ "
                                        "ከተማ/ሰፈር ይፃፉ።",
        "vehicle.location.gps_pin": "በካርታ (GPS) የተላከ አድራሻ",

        "vehicle.list.price_sell_prompt": "💵 **የመሸጫ ዋጋ**\n\nየመሸጫ ዋጋዎን ምንዛሬውን በETB/USD በመለየት ያስገቡ (ምሳሌ፡ 1,500,"
                                          "000 ETB ወይም 10,150 USD)።",
        "vehicle.list.price_rent_prompt": "💵 **የቀን ኪራይ ዋጋ**\n\nየቀን ኪራይ ዋጋዎን ምንዛሬውን በETB/USD በመለየት ያስገቡ (ምሳሌ፡ 3,"
                                          "000 ETB ወይም 50 USD)።",
        "vehicle.find.budget_buy_prompt": "💵 **ከፍተኛ የመግዣ በጀት**\n\nለመግዛት የመደቡትን ከፍተኛ በጀት ምንዛሬውን በETB/USD በመለየት ያስገቡ ("
                                          "ምሳሌ፡ 1,500,000 ETB ወይም 10,150 USD)።",
        "vehicle.find.budget_rent_prompt": "💵 **ከፍተኛ የቀን ኪራይ በጀት**\n\nለቀን ኪራይ የመደቡትን ከፍተኛ በጀት ምንዛሬውን በETB/USD በመለየት "
                                           "ያስገቡ (ምሳሌ፡ 3,000 ETB ወይም 50 USD)።",
        "vehicle.error.invalid_price": "⚠️ የተሳሳተ ዋጋ። እባክዎ ቁጥር ብቻ ያስገቡ።",

        "vehicle.list.photo_prompt": "📸 **የተሽከርካሪው ፎቶ**\n\nእባክዎ የተሽከርካሪውን ግልጽ ፎቶ ይላኩ። ፎቶ ከሌለዎት 'ዘልል' ብለው ይፃፉ።",
        "vehicle.photo.uploading": "⏳ ፎቶውን ወደ ማከማቻ በመጫን ላይ...",
        "vehicle.photo.upload_timeout": "⚠️ ፎቶውን መጫን አልተቻለም። ያለ ፎቶ እንቀጥላለን።",

        "vehicle.error.timeout": "⚠️ ከቴሌግራም ጋር ሲገናኙ የኔትወርክ ጊዜ አልፏል። ጥያቄዎ ተጠብቋል፤ እባክዎ እንደገና ይሞክሩ።",
        "vehicle.error.telegram_api": "⚠️ የቴሌግራም ግንኙነት ስህተት አጋጥሟል። እባክዎ እንደገና ይሞክሩ።",
        "vehicle_seeker_request_selected": "📋 <b>የተመረጠው የፈላጊ ጥያቄ:</b>",
        "preferred_mmy": "🚘 <b>የተመረጠው ሞዴል/ዓ.ም:</b>",
        "preferred_fuel": "⛽ <b>የተመረጠው የነዳጅ ዓይነት:</b>",
        "location_route": "📍 <b>ቦታ / መስመር:</b>",
        "max_budget": "💰 <b>ከፍተኛ የበጀት መጠን:</b>",
        "select_option_offer": "👇 <b>የዋጋ ማቅረቢያዎን ለማስገባት ከታች ካሉት አማራጮች አንዱን ይምረጡ፦</b>",
        'vehicle_selected': "✅ <b>ተሽከርካሪ ተመርጧል</b>",
        'type': "📂 <b>አይነት:</b>",
        'listing_mmy': "🚗 <b>የዝርዝር ሞዴል/ዓመት:</b>",
        'listing_fuel': "⛽ <b>የነዳጅ ዓይነት:</b>",
        'location': "📍 <b>አድራሻ:</b>",
        'price': "💰 <b>ዋጋ:</b>",

        # Status & System Alerts
        "msg_request_inactive": "⚠️ <b>የተመረጠው የፈላጊ ጥያቄ አሁን ላይ ንቁ አይደለም።</b>",
        "msg_vehicle_inactive": "⚠️ <b>የተመረጠው ተሽከርካሪ አሁን ላይ ንቁ አይደለም።</b>",
        # Section Headers
        "seeker_match_details_header": "👤 የፈላጊ ግጥሚያ ዝርዝሮች",
        "vehicle_option_details_header": "🚗 የተሽከርካሪ አማራጭ ዝርዝሮች",
        # Property & Field Labels
        "lbl_your_listed_vehicle": "የመዘገቡት ተሽከርካሪ",
        "lbl_requested_spec": "🎯 የተጠየቀው መግለጫ:",
        "lbl_proposed_spec": "🚙 የእርስዎ የቀረበ መግለጫ:",
        "lbl_your_requested_spec": "🎯 የእርስዎ የተጠየቀ መግለጫ:",
        "lbl_offered_vehicle_spec": "🚜 የቀረበው ተሽከርካሪ መግለጫ:",
        "lbl_fuel_type": "⛽ የነዳጅ ዓይነት:",
        "lbl_your_vehicle": "የእርስዎ ተሽከርካሪ",
        "lbl_location_route": "📍 ቦታ/መስመር:",
        "lbl_location": "📍 ቦታ:",
        "lbl_seeker_budget": "💰 የፈላጊው በጀት:",
        "lbl_flexible": "ተለዋዋጭ",
        # Action Prompts & Footers
        "match_flexibility_note": "💡 ማስታወሻ: ይህ ግጥሚያ መግለጫው በትንሹ ቢለያይም ተሽከርካሪዎን ለማቅረብ ያስችላል።",

        'select_option_owner': "ለባለቤቱ አቅርቦትዎን ለማስገባት ከታች ያለውን ይምረጡ:",
        'enter_custom_offer_btn': "🤝 የራስዎን አቅርቦት ያቅርቡ",
        'send_asking_rate': "✅ የጠየቁትን ዋጋ ይላኩ ({price} {currency})",
        'accept_asking_rate': "✅ የጠየቁትን ዋጋ ይቀበሉ ({price} {currency})",
        'back_to_matches': "❌ ወደ ተስማሚዎች ይመለሱ",
        'decline_deal': "❌ ስምምነቱን ውድቅ ያድርጉ",
        'prompt_offer_title': "💬 <b>ያቀረቡትን ዋጋ ያስገቡ</b>",
        'prompt_seeker_offer_title': "💬 <b>ለዚህ ጠያቂ ያቀረቡትን ዋጋ ያስገቡ</b>",
        'seeker_request_label': "👤 ጥያቄ:",
        'seeker_budget_label': "💰 የጠያቂ በጀት:",
        'asking_rate_label': "💰 የጠየቁት ዋጋ:",
        'prompt_reply_instructions': "እባክዎ በ {currency} መጠን ብቻ መልስ ይስጡ (ቁጥሮች ብቻ፣ ለምሳሌ: <code>5000</code>):",
        'invalid_amount': "⚠️ <b>ልክ ያልሆነ የቁጥር ቅርጸት።</b>\nእባክዎ ቁጥሮችን ብቻ ያስገቡ።\n*(ምሳሌ: 5000)*",

        'transmitting': "📡 <i>ጥያቄዎ በመተላለፍ ላይ ነው እና የውል ክፍለ ጊዜ እየተፈጠረ ነው...</i>",
        'err_seeker_listing': "⚠️ <b>የስርዓት ስህተት:</b> የባለጥያቄው ወይም የዝርዝሩ መታወቂያ አልተገኘም።",
        'err_provider': "⚠️ <b>የስርዓት ስህተት:</b> የተሽከርካሪ ባለቤቱን ማረጋገጥ አልተቻለም።",
        'seeker_proposal_title': "📩 <b>አዲስ የተሽከርካሪ ሃሳብ ደርሷል</b>",
        'seeker_proposal_desc': "አንድ የተሽከርካሪ ባለቤት ለጠየቁት ጥያቄ ዋጋ አቅርቧል!",
        'proposed_rate': "💰 <b>የቀረበው ዋጋ:</b>",
        'requested_veh': "🎯 <b>የጠየቁት:</b>",
        'offered_veh': "🚗 <b>የቀረበው:</b>",
        'note_diff': "💡 <i>ማስታወሻ: ከመጀመሪያው ጥያቄዎ ሊለያይ ስለሚችል በጥንቃቄ ይመርምሩ።</i>",

        'forward_offer_transmitted': "✅ <b>ጥያቄዎ ተላልፏል!</b>\n\nለ <i>{asset_title}</i> ያቀረቡት የ <b>{formatted_price}</b> "
                                     "ዋጋ ደርሷል።",
        'provider_offer_title': "📩 <b>በተሽከርካሪዎ ላይ አዲስ ዋጋ ቀርቧል</b>",
        'provider_offer_desc': "አንድ ባለጥያቄ በተሽከርካሪዎ ዝርዝር ላይ ዋጋ አቅርቧል!",
        'offered_amount': "💰 <b>የቀረበው መጠን:</b>",
        'listed_veh': "📝 <b>የተዘረዘረው:</b>",
        'seeker_desired_veh': "🎯 <b>የባለጥያቄው ፍላጎት:</b>",

        'accept_btn': "✅ ተቀበል",
        'counter_btn': "💬 የዋጋ ግምት አቅርብ",
        'decline_btn': "❌ ተወው",

        'deal_blocked': "⚠️ <b>የውል ክፍለ ጊዜ መጀመር አልተቻለም። እባክዎ እንደገና ይሞክሩ።</b>",
        'system_err': "⚠️ <b>የስርዓት ስህተት ተፈጥሯል። እባክዎ ቆየት ብለው ይሞክሩ።</b>",

        'new_provider_offer_title': "🔔 <b>አዲስ ከአቅራቢ የቀረበ አቅርቦት!</b>",
        'new_provider_offer_intro': "አንድ አቅራቢ የ <b>{formatted_price}</b> ዋጋ አቅርቧል።",
        'requested_specs_header': "📌 <b>እርስዎ የጠየቁት (የተመረጠ) መግለጫ:</b>",
        'offered_specs_header': "🚗 <b>የቀረበው ዝርዝር መግለጫ:</b>",
        'spec_mmy': "  • ሞዴል/ዓመት: {val}",
        'spec_fuel': "  • የነዳጅ ዓይነት: {val}",
        'choose_action': "እባክዎ ለመመልስ ከታች ያለውን እርምጃ ይምረጡ:",
        'accept_price_btn': "✅ ዋጋውን ይቀበሉ",
        'counter_offer_btn': "📉 ተቃራኒ ዋጋ ያቅርቡ",
        "transmitting_offer": "🔄 <i>የዋጋ ማቅረቢያ በመላክ ላይ ይገኛል...</i>",
        "offer_transmitted": "✅ <b>የዋጋ ማቅረቢያዎ ተልኳል!</b>\n\nለ <i>{asset_title}</i> ያቀረቡት <b>{formatted_price}</b> "
                             "ደህንነቱ በተጠበቀ መልኩ ተልኳል።",
        "provider_offer_transmitted": "✅ <b>የዋጋ ማቅረቢያዎ በተሳካ ሁኔታ ተልኳል!</b>\n\nለ <b>{list_mmy}</b> ያቀረቡት <code>{"
                                      "formatted_price}</code> ለፈላጊው ተልኳል።",
        'new_offer_on_listing_title': "🔔 <b>በዝርዝርዎ ላይ አዲስ አቅርቦት አለ!</b>",
        'new_offer_on_listing_intro': "አንድ ጠያቂ የ <b>{formatted_price}</b> አቅርቦት አቅርቧል።",
        'your_listing_specs_header': "🚗 <b>የእርስዎ ዝርዝር መግለጫዎች:</b>",
        'seekers_requested_specs_header': "📌 <b>የጠያቂው የጠየቁት መግለጫዎች:</b>",
        'accept_offer_btn': "✅ አቅርቦቱን ይቀበሉ",
        'negotiation_blocked': "🚫 <b>ድርድር ታግዷል</b>\n\nይህ ንብረት በአሁኑ ጊዜ በድርድር ላይ ነው።",
        'system_error': "⚠️ የስርዓት ስህተት: ድርድር መጀመር አልተቻለም።",
        "vehicle.list.contact_prompt": "📞 **የመገናኛ ስልክ**\n\nደንበኞች እንዲያገኙዎ እባክዎ ስልክ ቁጥርዎን ያስገቡ (ለምሳሌ 0911234567)።",
        "vehicle.find.contact_prompt": "📞 **የመገናኛ ስልክ**\n\nአቅራቢዎች እንዲያገኙዎ እባክዎ ስልክ ቁጥርዎን ያስገቡ (ለምሳሌ 0911234567)።",
        "vehicle.sync.fleet_records": "⚙️ መረጃዎን ከስርዓቱ ጋር በማቀናጀት ላይ...",
        "vehicle.list.success": "🎉 **ተመዝግቧል!**\n\nየእርስዎ {year_model} ({vtype}) በተሳካ ሁኔታ ተመዝግቧል።",
        "vehicle.find.interrogating": "🔍 ስርዓቱን በመቃኘት ተስማሚ ተሽከርካሪዎችን በመፈለግ ላይ...",
        "vehicle.find.no_match": "📡 ጥያቄዎ ተመዝግቧል። አሁን ላይ ተስማሚ መኪና አልተገኘም፣ ነገር ግን ሲገኝ እናሳውቆታለን።",

        "MSG_TOP_VEHICLE_SEEKER_MATCHES": "🎯 <b>ከፍተኛ {count} የ{vertical} ፈላጊዎች "
                                          "ተገኝተዋል!</b>\n──────────────────────────────\nእነዚህ ፈላጊዎች ተሽከርካሪ ይፈልጋሉ። "
                                          "የራስዎን ተሽከርካሪ ለማቅረብ ከታች ካሉት የፈላጊ ጥያቄዎች አንዱን ይምረጡ፡",
        # --- DOMAIN / VERTICAL NAMES ---
        "domain_housing": "ቤት",
        "domain_vehicle": "ተሽከርካሪ",
        "domain_equipment": "ማሽነሪ",
        "vehicle.btn.lock_vehicle": "🔐 አረጋግጥ እና ክፈል",
        "vehicle.btn.decline_match": "❌ አልፈልግም",
        "vehicle.label.daily_rental": "የቀን ኪራይ",
        "vehicle.label.purchase_price": "የመሸጫ ዋጋ",
        "vehicle.find.match_found": "🎯 **ተስማሚ ተሽከርካሪ ተገኝቷል!** (ተዛምዶ: {score}%)\n\n**ተሽከርካሪ:** {asset}\n**ነዳጅ:** {"
                                    "fuel}\n**አድራሻ:** {location}\n**{price_label}:** {price} ብር\n\nይህን ተሽከርካሪ ማግኘት "
                                    "ይፈልጋሉ?",
        "vehicle.error.pipeline_crash": "❌ የውስጥ ችግር አጋጥሟል። እባክዎ ሂደቱን እንደገና ይጀምሩ።",
        "vehicle.escrow.syncing": "⚙️ የክፍያ መጠየቂያ በማዘጋጀት ላይ...",
        "vehicle.btn.open_portal": "💳 ክፍያ ፈጽም",
        "vehicle.escrow.initialized": "🔐 **ክፍያ**\n\nየአቅራቢውን ትክክለኛ ስልክ ቁጥር ለማግኘት፣ እባክዎ የፕላትፎርም ኮሚሽን ይክፈሉ።",
        "vehicle.escrow.failed": "❌ የክፍያ ማስፈጸሚያ ማዘጋጀት አልተቻለም። እባክዎ እንደገና ይሞክሩ።",
        "vehicle.cancel_message": "🛑 ሂደቱ ተቋርጧል።",
        "vehicle.btn.send_offer": "🤝 ለባለቤቱ ጥያቄ ይላኩ",
        "vehicle.find.top_matches_found": "🎯 ለእርስዎ እነዚህን ተስማሚ ምርጫዎች አግኝተናል። እባክዎ አንዱን ይምረጡ፦",
        "vehicle.negotiation.syncing": "🔄 ጥያቄው ወደ ተሽከርካሪው ባለቤት እየተላከ ነው...",
        "vehicle.negotiation.started": "✅ **ጥያቄው ተልኳል!**\n\nየጠየቁት `{price} ብር` ጥያቄ ወደ ተሽከርካሪው ባለቤት ተልኳል። ባለቤቱ ጥያቄዎን "
                                       "ይገመግማል፤ **ይቀበላል**፣ **የራሱን ዋጋ ያቀርባል** ወይም **ይቀንሳል**።\n\nምላሽ ሲሰጡ ወዲያውኑ ማሳወቂያ "
                                       "ይደርሶታል::",

        # ==============================================
        # --- FREIGHT & RIDE (logistics_) ---
        # ==============================================
        # ----------------------------------------------------
        # GLOBAL NAVIGATION & OFFERS
        # ----------------------------------------------------
        "RETURN_MAIN_MENU": "ወደ ዋናው ማውጫ በመመለስ ላይ...",
        "FREIGHT_OFFER_PROMPT": "🤝 **ዋጋ ያቅርቡ**\n\nእባክዎ የመነሻ ዋጋዎን በብር ያስገቡ (ቁጥር ብቻ)።",
        "BTN_CANCEL_OFFER": "❌ ጥያቄውን ሰርዝ",
        "ERR_INVALID_NUMBER": "⚠️ የተሳሳተ መጠን። እባክዎ ትክክለኛ ቁጥር ብቻ ያስገቡ።",
        "ERR_LISTING_UNAVAILABLE": "⚠️ ይህ መረጃ አሁን ላይ አይገኝም።",
        "ERR_OWN_LISTING": "⚠️ በራስዎ ማስታወቂያ ላይ ዋጋ ማቅረብ አይችሉም።",
        "ERR_SYS_NEGOTIATION_OFFLINE": "⚠️ የዋጋ ድርድር ስርዓቱ አሁን ላይ አይሰራም። እባክዎ ትንሽ ቆይተው ይሞክሩ።",
        "MSG_OFFER_SENT": "✅ የ **{amount} ብር** ጥያቄዎ ለአቅራቢው ተልኳል። ምላሽ ሲሰጡ እናሳውቆታለን።",
        "ERR_OFFER_FAILED": "❌ ጥያቄዎን መላክ አልተቻለም። እባክዎ እንደገና ይሞክሩ።",
        "MSG_PROCESS_CANCELLED": "🛑 ሂደቱ ተቋርጧል። ወደ ዋናው ማውጫ በመመለስ ላይ...",

        # ----------------------------------------------------
        # ROLE SELECTION
        # ----------------------------------------------------
        "MSG_FREIGHT_START": "🚛 **የጭነት መጓጓዣ**\n\nዕቃ ማጓጓዝ ይፈልጋሉ ወይስ የጭነት መኪና አገልግሎት መስጠት?",
        "BTN_ROLE_SHIPPER": "📦 የጭነት መኪና ፈላጊ",
        "BTN_ROLE_CARRIER": "🚚 አጓጓዥ (ባለመኪና)",

        "MSG_RIDE_START": "🚕 **የትራንስፖርት አገልግሎት**\n\nትራንስፖርት እየፈለጉ ነው ወይስ አገልግሎት መስጠት ይፈልጋሉ?",
        "BTN_ROLE_PASSENGER": "🧍 ትራንስፖርት ፈላጊ",
        "BTN_ROLE_DRIVER": "🚗 አሽከርካሪ (አገልግሎት ሰጪ)",

        # ----------------------------------------------------
        # ORIGIN & DESTINATION
        # ----------------------------------------------------
        "PROMPT_ORIGIN_SHIPPER": "📍 **መነሻ ቦታ**\n\nየሚጫነው ዕቃ አሁን የት ነው ያለው?",
        "PROMPT_ORIGIN_CARRIER": "📍 **መነሻ ቦታ**\n\nመኪናዎ በአሁኑ ጊዜ የት ነው የሚገኘው ወይም ከየት ነው የሚንቀሳቀሰው?",
        "PROMPT_ORIGIN_PASSENGER": "📍 **መነሻ ቦታ**\n\nየት ቦታ ነው የሚያነሳዎት?",
        "PROMPT_ORIGIN_DRIVER": "📍 **የአሁኑ መገኛ**\n\nአሁን የት አካባቢ ነው ያሉት?",
        "ERR_VAGUE_ADDRESS": "⚠️ ያስገቡት አድራሻ ግልጽ አይደለም። እባክዎ የተወሰነ ቦታ ያስገቡ።",

        "PROMPT_DEST_SHIPPER": "📍 **መድረሻ**\n\nከ {origin} ተነስቶ የት ነው የሚደርሰው?",
        "PROMPT_DEST_CARRIER": "📍 መነሻዎ {origin} ተረጋግጧል። እባክዎን የሚፈልጉትን የመድረሻ ቦታ ወይም የሥራ ክልል ይግለጹ:",
        "PROMPT_DEST_PASSENGER": "📍 **መድረሻ**\n\nከ {origin} ተነስተው የት ነው የሚሄዱት?",
        "PROMPT_DEST_DRIVER": "📍 **መድረሻ**\n\nከ {origin} ተነስተው ወደ የት እያቀኑ ነው?",
        "ERR_INVALID_DESTINATION": "⚠️ የመድረሻ አድራሻው ግልጽ አይደለም። እባክዎ በግልጽ ይፃፉ።",
        "PROMPT_ENTER_OPERATING_REGION": "እባክዎ የሚሰሩበትን ወይም የሚመርጡትን ክልል/አካባቢ ያስገቡ። (ምሳሌ፦ ሶማሌ ወይም ከጅጅጋ እስከ አዲስ አበባ",
        # ----------------------------------------------------
        # GPS TELEMETRY
        # ----------------------------------------------------
        "BTN_SHARE_GPS": "📍 በካርታ (GPS) ቦታዎን ያጋሩ",
        "MSG_GPS_LOCKED": "✅ አድራሻዎ በተሳካ ሁኔታ ተመዝግቧል።",
        "MSG_GPS_BYPASSED": "⚠️ የካርታ (GPS) አድራሻ አልተሰጠም። በፃፉት አድራሻ እንቀጥላለን።",
        "MSG_COORDS_LOCKED": "🛰️ የቦታው ኮኦርዲኔት ተመዝግቧል: **{lat:.6f}° N, {lon:.6f}° E**",
        "PROMPT_GPS_CALIBRATION": (
            "🛰️ **የጂፒኤስ ማስተካከያ**\n\n"
            "• **በስልክ ለሚጠቀሙ:** ከታች ያለውን አዝራር በመጫን ቦታዎን ያጋሩ።\n"
            "• **በኮምፒውተር / በጽሑፍ:** የመኖሪያ ሰፈርዎን ወይም ኮኦርዲኔት ይጻፉ\n"
            "  *(ምሳሌ፡ `Latitude 8.514477° N, Longitude 39.269257° E` ወይም `8° 30' 52\" N, 39° 16' 9\" E`)*።"
        ),

        # ----------------------------------------------------
        # CAPACITY & SPECS
        # ----------------------------------------------------
        "PROMPT_CAPACITY_SHIPPER": "⚖️ **የዕቃው ክብደት**\n\nየዕቃው ግምታዊ ክብደት በኪሎግራም (kg) ስንት ነው? (ቁጥር ብቻ)",
        "PROMPT_CAPACITY_CARRIER": "⚖️ **የመጫን አቅም**\n\nተሽከርካሪዎ ቢበዛ ስንት ኪሎግራም (kg) መጫን ይችላል? (ቁጥር ብቻ)",
        "PROMPT_CAPACITY_PASSENGER": "👥 **የተጓዥ ብዛት**\n\nስንት ሰዎች ናችሁ? (ቁጥር ብቻ)",
        "PROMPT_CAPACITY_DRIVER": "💺 **ክፍት ወንበር**\n\nበመኪናዎ ላይ ስንት ክፍት ወንበሮች አሉ? (ቁጥር ብቻ)",
        "ERR_INVALID_NUMBER_CLEAN": "⚠️ የተሳሳተ ቅርጸት። እባክዎ ትክክለኛ ቁጥር ያስገቡ።",

        # ----------------------------------------------------
        # VEHICLE CLASSES & EXTRAS
        # ----------------------------------------------------
        "PROMPT_CLASS_FREIGHT": "🚛 **የተሽከርካሪ ዓይነት/የተሽከርካሪ ምድብ**\n\nየሚፈለገው ወይም የሚቀርበው የተሽከርካሪ/የማጓጓዣ ዓይነት በግልጽ የትኛው "
                                "ነው? (ለምሳሌ፦ ባጃጅ ካርጎ፣ ኢሱዙ ኤፍ.ኤስ.አር (Isuzu FSR)፣ ኤፍ.ኤስ.አር (FSR)፣ ድራይ ቫን፣ ፍላትቤድ፣ "
                                "ሪፍሪጅሬትድ፣ ቦክስ ትራክ)",
        "PROMPT_CLASS_RIDE": "🚘 **የመኪና ዓይነት/የጉዞ ምድብ**\n\nምን ዓይነት መኪና ነው የሚፈልጉት/ያሎት? (ለምሳሌ፦ ኢኮኖሚ፣ ኮምፈርት/ምቹ ፣ ሚኒባስ/ቅንጡ)",
        "ERR_DESC_TOO_BRIEF": "⚠️ መግለጫው አጭር ነው። እባክዎ የተሽከርካሪውን ዓይነት በግልጽ ይፃፉ።",

        "PROMPT_EXTRAS_FREIGHT": "🚀 **ልዩ ፍላጎቶች**\n\nየማንኛውም ልዩ አያያዝ  ፍላጎት ወይም የጭነት አቅም በነጠላ ሰረዝ በመለየት ይዘርዝሩ (ምሳሌ፦ "
                                 "በቀላሉ የሚሰበር፣ አደገኛ ዕቃ፣ የጭነት እርዳታ)። መደበኛ ከሆነ 'None' ብለው ይጻፉ።",
        "PROMPT_EXTRAS_RIDE": "✨ **ተጨማሪ አገልግሎቶች እና ጥቅማጥቅሞች**\n\nየሚያስፈልጉትን ወይም የሚቀርቡትን ተጨማሪ አገልግሎቶች በነጠላ ሰረዝ በመለየት "
                              "ይዘርዝሩ (ለምሳሌ፦ ኤሲ/የአየር ማቀዝቀዣ፣ የቤት እንስሳ የሚፈቅድ፣ ተጨማሪ ሻንጣ)። መደበኛ ከሆነ 'የለም' ብለው ይፃፉ።",
        "KEYWORD_HAZMAT": "አደገኛ",
        "KEYWORD_NONE": "የለም",

        # ----------------------------------------------------
        # BUDGET & CONTACT
        # ----------------------------------------------------
        "PROMPT_BUDGET_SHIPPER": "💵 **የመጓጓዣ በጀት**\n\nለዚህ ጭነት የመደቡትን ከፍተኛ በጀት የገንዘብ ዓይነቱን በETB ወይም በUSD/$ በመጥቀስ ያስገቡ።",
        "PROMPT_BUDGET_CARRIER": "💵 **የማጓጓዣ ዋጋ**\n\nለዚህ ጉዞ የሚጠይቁትን ግምታዊ ዋጋ የገንዘብ ዓይነቱን በETB ወይም በUSD/$ በመጥቀስ ያስገቡ።",
        "PROMPT_BUDGET_PASSENGER": "💵 **ከፍተኛ የትራንስፖርት በጀት**\n\nለዚህ ጉዞ የመደቡትን ከፍተኛ በጀት የገንዘብ ዓይነቱን በETB ወይም በUSD/$ "
                                   "በመጥቀስ ያስገቡ።",
        "PROMPT_BUDGET_DRIVER": "💵 **ግምታዊ ክፍያ**\n\nለዚህ ጉዞ የሚጠይቁትን ክፍያ የገንዘብ ዓይነቱን በETB ወይም በUSD/$ በመጥቀስ ያስገቡ።",
        "ERR_INVALID_BUDGET": "⚠️ የተሳሳተ ዋጋ። እባክዎ ቁጥር ብቻ ያስገቡ።",
        "MSG_REQUEST_PLATE": "🚗 እባክዎ የትሽከርካሪዎን የሰሌዳ ቁጥር ያስገቡ (ለምሳሌ፦ AA-3-B12345)፦",
        "ERR_INVALID_PLATE": "⚠️ የተሳሳተ የሰሌዳ ቁጥር ቅርጸት። እባክዎ ትክክለኛ የሰሌዳ ቁጥር እንደገና ያስገቡ፦",
        "prompt_vehicle_photo": "📸 እባክዎ ግልጽ የሆነ የተሽከርካሪ ፎቶ ይላኩ ወይም ያለ ፎቶ ለመቀጠል 'Skip' ብለው ይፃፉ፦",
        "prompt_asset_photo": "📸 እባክዎ ግልጽ የሆነ የንብረት/ተሽከርካሪ ፎቶ ይላኩ ወይም ያለ ፎቶ ለመቀጠል 'Skip' ብለው ይፃፉ፦",
        "PROMPT_CONTACT_INFO": "📞 **የመገናኛ ስልክ**\n\nእባክዎ ስልክ ቁጥርዎን ያስገቡ (ለምሳሌ 0911234567)።",

        # ----------------------------------------------------
        # PIPELINE, MATCHING & CHECKOUT
        # ----------------------------------------------------
        "BTN_ACCEPT_MATCH": "🔐 አረጋግጥ እና ክፈል",
        "MSG_MATCH_FOUND": "🎯 **ተስማሚ ተገኝቷል!** (ተዛምዶ: {score}%)\n\n**ግምታዊ ክፍያ:** {fare}\n\nይህን አቅራቢ ማግኘት ይፈልጋሉ?",

        "MSG_SECURE_CHECKOUT_LOAD": "⚙️ የክፍያ መጠየቂያ በማዘጋጀት ላይ...",
        "BTN_PAYMENT_PORTAL": "💳 ክፍያ ፈጽም",
        "MSG_LOGISTICS_LOCKED": "🔐 **ስምምነት**\n\nየአቅራቢውን ትክክለኛ ስልክ ቁጥር ለማግኘት፣ እባክዎ የፕላትፎርም ኮሚሽን ይክፈሉ።",
        "ERR_CHECKOUT_FAILED": "❌ የክፍያ ማስፈጸሚያ ማዘጋጀት አልተቻለም። እባክዎ ቆየት ብለው ይሞክሩ።",

        # General & Status Messages
        "MSG_COMPILING_PROFILE": "⏳ መገለጫ በመጠናቀር ላይ እና የሎጂስቲክስ ኖዶች ይመሳሰላሉ...",
        "MSG_SCANNING_NODES": "🔍 የ<b>{domain}</b> ኖዶችን በመቃኘት እና የማዛመጃ ማትሪክስ በመመርመር ላይ...",
        "MSG_REGISTRATION_COMPLETE": "✅ **የ{domain} ንብረት ምዝገባ ተጠናቋል!**",
        "MSG_REQUEST_BROADCASTED": "📡 ጥያቄው ለ{domain} ኔትወርክ ተሰራጭቷል። አፋጣኝ ተዛማጆች አልተገኙም፣ ነገር ግን ተዛማጅ ሲገኝ እናሳውቆታለን።",
        "MSG_TOP_FREIGHT_MATCHES": "🎯 **ምርጥ {count} የ{localized_domain} ተስማሚዎች ተገኝተዋል!**\n\nእባክዎ አቅርቦት ለመላክ ከታች "
                                   "ከሚገኙት አማራጮች አንዱን ይምረጡ፡",

        # Domains
        "domain_freight": "የጭነት",
        "domain_ride": "የጉዞ",

        # Buttons & Labels
        "BTN_DECLINE_RETURN": "❌ አልፈልግም (ተመለስ)",
        "TITLE_TRANSPORT_ROUTE_FREIGHT": "የመጓጓዣ መንገድ: {origin} ➡️ {destination}",

        # Seeker Match Items
        "MSG_CARD_SEEKER_SELECTED_FREIGHT": "👤 <b>የፈላጊ ጥያቄ ተመርጧል:</b> {title}\n📍 <b>መንገድ:</b> <code>{origin} ➡️ {"
                                            "destination}</code>\n{details}💰 <b>ከፍተኛ ባጀት:</b> <code>{budget} {"
                                            "currency}</code>\n\n<i>አቅርቦትዎን ለዚህ ፈላጊ ለመላክ ከታች ካሉት አማራጮች አንዱን ይምረጡ፡</i>",
        "BTN_ACCEPT_DEAL": "✅ ዋጋውን ተቀበል",
        "BTN_COUNTER_OFFER": "📉 የመደራደሪያ ዋጋ አቅርብ",
        "BTN_DECLINE_DEAL": "❌ ውድቅ አድርግ",
        "NEGOTIATION_TITLE": "🔄 አዲስ የማቅረቢያ ጥያቄ",
        "TRANSACTION_LABEL": "ግብይት",
        "ASSET_LABEL": "ንብረት/ዕቃ",
        "OFFERED_PRICE": "የቀረበ ዋጋ",
        "EVALUATE_PROMPT": "እባክዎ የቀረበውን ዋጋ ተመልክተው ከታች ካሉት አማራጮች ውሳኔ ይስጡ።",
        "ERR_SYS_PIPELINE_FAILED": "⚠️ የሲስተም ችግር አጋጥሟል። እባክዎ በድጋሚ ይሞክሩ።",
        "err_vehicle_not_found": "⚠️ የተመረጠው መኪና አሁን አይገኝም።",
        "MSG_SELECTED_MATCH_SUMMARY": "{domain_emoji} <b>የተመረጠው ማጠቃለያ፡</b>\n<i>{asset_title}</i>\n\n💰 <b>የግምት "
                                      "ዋጋ፡</b> <code>{fare} ETB</code>\n\nየዋጋ ማቅረቢያዎን ለማስገባት ከታች ካሉት አማራጮች ውስጥ አንዱን "
                                      "ይምረጡ፡",
        "msg_enter_custom_offer": "💬 <b>የእርስዎን የዋጋ ማቅረቢያ ያስገቡ</b>",
        "MSG_PROMPT_CUSTOM_OFFER": "{header_text}\n\n<b>ዝርዝር መረጃ፡</b>\n<i>{asset_title}</i>\n\nየግምት ዋጋ፡ <code>{"
                                   "asking_price} ETB</code>\n\nእባክዎ የሚያቀርቡትን የገንዘብ መጠን በብር ያስገቡ (ለምሳሌ፡ "
                                   "<code>125000</code> ወይም <code>125,000</code>)፡",
        "err_invalid_price": "⚠️ <b>የገባው የገንዘብ መጠን ትክክል አይደለም</b>\n\nእባክዎ ቁጥር ብቻ ያስገቡ (ለምሳሌ፡ <code>1500</code> ወይም "
                             "<code>125,000</code>)።",
        "MSG_OFFER_TRANSMITTING": "⏳ <b>የዋጋ ማቅረቢያዎን በመላክ ላይ...</b>\n\nያቀረቡት <code>{custom_offer} ETB</code> ዋጋ ለአቅራቢው "
                                  "እየተላከ ነው። እባክዎ እስኪገመግሙት ይጠብቁ።",
        "ERR_VERIFY_PROVIDER": "⚠️ የሲስተም ስህተት፡ አገልግሎት ሰጪውን ማረጋገጥ አልተቻለም።",
        "MSG_TRANSMITTING_OFFER": "🔄 ጥያቄዎን ወደ አቅራቢው በማስተላለፍ ላይ...",
        "MSG_OFFER_TRANSMITTED_SUCCESS": "✅ <b>የዋጋ ማቅረቢያው ተልኳል!</b>\n\nያቀረቡት <b>{formatted_price} ETB</b> ዋጋ ለ <i>{"
                                         "safe_asset_title}</i> አገልግሎት ሰጪው ተላልፏል።",
        "ERR_PENDING_NEGOTIATION": "🚫 <b>ድርድሩ አልተፈቀደም</b>\n\nይህ አቅራቢ በአሁኑ ጊዜ በሌላ ድርድር ላይ ነው። እባክዎ ቆይተው እንደገና ይሞክሩ።",
        "ERR_DEAL_INIT_FAILED": "⚠️ የሲስተም ስህተት፡ ድርድሩን መጀመር አልተቻለም።",
        "MSG_PROPOSE_OFFER_HEADER": "የድርድር ዋጋ ያቅርቡ",
        "LBL_ASSET": "ንብረት / ዕቃ",
        "LBL_ASKING_RATE": "የሚጠየቀው ዋጋ",
        "MSG_PROPOSE_OFFER_INSTRUCTION": "ዝርዝሩን አይተው ዋጋውን ይቀበሉ ወይም ማቅረቢያዎን ያቅርቡ።",

        # Verticals
        "vertical_housing": "ቤት",
        "vertical_vehicle": "ተሽከርካሪ",
        "vertical_equipment": "ዕቃዎች",
        "vertical_freight": "ጭነት",
        "vertical_ride": "ራይድ",

        # Transactions
        "tx_rental": "ኪራይ",
        "tx_sale": "ሽያጭ",
        "tx_lease": "ሊዝ",

        # Logistics Labels
        "lbl_route": "መስመር",
        "lbl_plate": "የሰሌዳ ቁጥር",
        "lbl_weight": "የጭነት ክብደት",
        "lbl_party": "ተሳፋሪዎች ብዛት",
        "lbl_category": "ምድብ",
        "lbl_transaction": "ግብይት",

        "LABEL_ROUTE": "መስመር",
        "LABEL_PLATE": "የሰሌዳ ቁጥር",
        "LABEL_WEIGHT": "ክብደት",
        "LABEL_PARTY_SIZE": "የተሳፋሪዎች ብዛት",

        # --- Requests & Processing ---
        "SYS_PROCESSING": "⏳ <b>ጥያቄዎ በመከናወን ላይ ይገኛል...</b>\n\n<i>እባክዎ የድርድር ሂደቱን በአስተማማኝ ሁኔታ ስናዘጋጅ እና የክፍያ ሊንኮችን "
                          "ስናመነጭ በትዕግስት ይቆዩ።</i> 🔄",
        "ERR_MISSING_SEEKER_LISTING": "⚠️ የሲስተም ስህተት፡ የተረጋገጠ ፈላጊ ወይም መለያ ጠፍቷል።",
        "ERR_NEGOTIATION_UNAVAILABLE": "⚠️ የሲስተም ስህተት፡ የድርድር አገልግሎቱ በአሁኑ ጊዜ አይገኝም።",
        "ERR_SYNC_STREAM": "⚠️ የሲስተም ስህተት፡ የተመሳሰለ የድርድር ፍሰት መጀመር አልተቻለም።",

        # --- Success Messages ---
        "SUCCESS_PROVIDER_OFFER": (
            "✅ <b>የአቅራቢ ዋጋ ተልኳል!</b>\n\n"
            "🏠 <b>ንብረት/ጥያቄ፡</b> <i>{escaped_title}</i>\n"
            "💰 <b>የቀረበ ዋጋ፡</b> <b>{formatted_price} {currency}</b>\n\n"
            "የእርስዎ መወዳደሪያ ዋጋ በተሳካ ሁኔታ ለፈላጊው ደርሷል።"
        ),
        "SUCCESS_DEAL_BID": (
            "✅ <b>የፈላጊ ዋጋ ተልኳል!</b>\n\n"
            "🏠 <b>ንብረት/አገልግሎት፡</b> <i>{escaped_title}</i>\n"
            "💰 <b>የቀረበ ዋጋ፡</b> <b>{formatted_price} {currency}</b>\n\n"
            "የእርስዎ መወዳደሪያ ዋጋ ወደ ድርድር ሞጁሉ በተሳካ ሁኔታ ተላልፏል።"
        ),

        # --- Pipeline Blocks ---
        "BLOCK_NEGOTIATION": (
            "🚫 <b>የድርድር ሂደቱ ተገድቧል</b>\n\n"
            "ይህ ንብረት በአሁኑ ጊዜ በነቃ ድርድር ላይ ነው፣ ወይም በራስዎ ማስታወቂያ ላይ ዋጋ ማቅረብ አይችሉም።"
        ),

        # --- Cards & Detail Labels ---
        "LABEL_CARGO_WEIGHT": "የጭነት ክብደት",

        # --- Common Values / Fallbacks ---
        "VAL_NEGOTIABLE": "ተደራዳሪ",
        "VAL_ORIGIN_DEFAULT": "መነሻ ነጥብ",
        "VAL_DEST_DEFAULT": "መድረሻ ነጥብ",

        "deal_agreed_provider_confirmation": (
            "✅ ለ <i>{asset_title}</i> የ <b>{price} {currency}</b> ማቅረቢያን ተቀብለዋል። "
            "የፈላጊ ክፍያን በመጠበቅ ላይ።\n{details}"
        ),
        "deal_agreed_seeker_invoice": (
            "🎉 <b>ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\n"
            "አቅራቢው ለ <i>{asset_title}</i> የ <b>{price} {currency}</b> ማቅረቢያዎን ተቀብለዋል።\n"
            "{details}\nተጨማሪ መረጃ እና የአቅራቢውን አድራሻ (የስልክ ቁጥር) ለማግኘት፣ እባክዎ የፕላትፎርሙን ኮሚሽን ይክፈሉ።"
        ),
        "deal_agreed_seeker_confirmation": (
            "✅ <b>የመደራደሪያ ዋጋ ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\n"
            "ለ <i>{asset_title}</i> የ <b>{price} {currency}</b> ተቃራኒ ዋጋ ተቀብለዋል።\n"
            "{details}\nተጨማሪ መረጃ እና የአቅራቢውን አድራሻ (የስልክ ቁጥር) ለማግኘት፣ እባክዎ የፕላትፎርሙን ኮሚሽን ይክፈሉ።"
        ),
        "deal_agreed_provider_notice": (
            "🎉 <b>የመደራደሪያ ዋጋ ማቅረቢያ ተቀባይነት አግኝቷል!</b>\n\nፈላጊው ለ <i>{asset_title}</i> ያቀረቡትን "
            "የ <b>{price} {currency}</b> ተቃራኒ ዋጋ ተቀብሏል።\n{details}\nክፍያ በመጠበቅ ላይ።"
        ),
        "btn_pay_local": "🇪🇹 ሀገር ውስጥ ክፍያዎች ({price} ETB)",
        "btn_pay_intl": "🌍 ዓለም አቀፍ ካርድ / PayPal",

        # ----------------------------------------------------
        # WEBHOOKS
        # ----------------------------------------------------
        "DEFAULT_DRIVER_NAME": "የፕላትፎርሙ አሽከርካሪ",
        "MSG_WEBHOOK_SUCCESS": "✅ **ክፍያዎ ተረጋግጧል!**\n\nየመለያ ቁጥር: `{deal_id}`\n\n**አሽከርካሪ/አጓጓዥ:** {"
                               "driver_name}\n**ስልክ:** {contact}\n**የሰሌዳ ቁጥር:** {vehicle_plate}\n\nስለተጠቀሙ እናመሰግናለን።",

        # ================================================
        # --- MATCHING & DEALS (deal_) ---
        # ================================================
        "deal_match_found": "🎉 *ተዛማጅ ተገኝቷል!*\n\nለጥያቄዎ የሚሆን ተዛማጅ አግኝተናል።",
        "deal_proposed": "🤝 *አዲስ ስምምነት ቀርቧል!*\n\nከታች ያለውን ዝርዝር ይመልከቱ:",
        "deal_btn_accept": "✅ ስምምነቱን ተቀበል",
        "deal_btn_decline": "❌ ስምምነቱን ውድቅ አድርግ",
        "deal_accepted_seeker": "✅ ስምምነቱን ተቀብለዋል! የሌላኛውን ወገን ማረጋገጫ በመጠባበቅ ላይ...",
        "deal_accepted_provider": "🎉 *ስምምነቱ ጸድቋል!* ሁለቱም ወገኖች ተስማምተዋል። የመገናኛ መረጃዎ ይኸው:",
        "deal_declined": "🚫 ስምምነቱ ውድቅ ተደርጓል። ሌሎች አማራጮችን መፈለጋችንን እንቀጥላለን።",
        "deal_contact_info": "📞 *የመገናኛ መረጃ:*\nስም: {name}\nስልክ: {phone}",

        "TITLE_TRANSPORT_ROUTE": "የትራንስፖርት መስመር: {origin} ➡️ {destination}",
        "MSG_CARD_MATCH_SUMMARY_FREIGHT": (
            "🚚 <b>የተመረጠው ማጠቃለያ:</b>\n\n"
            "{title}\n"
            "📍 <b>መስመር:</b> {origin} ➡️ {destination}\n"
            "💰 <b>የተገመተ ዋጋ:</b> <b>{fare} {currency}</b>\n\n"
            "የመጓጓዣ ጥያቄዎን ለመላክ ከታች ይምረጡ:"
        ),
        "BTN_SEND_ASKING_RATE": "✅ የተጠየቀውን ዋጋ ላክ ({price} {currency})",
        "BTN_ACCEPT_FARE": "✅ ሂሳቡን ተቀበል ({fare} {currency})",
        "BTN_BACK_TOP_MATCHES": "❌ ወደ ተመራጭ ውጤቶች ተመለስ",
        "ERR_REQUEST_NOT_FOUND": "⚠️ የተመረጠው ጥያቄ ተሰርዟል ወይም ተጠናቋል።",
        "ERR_VEHICLE_NOT_FOUND": "⚠️ የተመረጠው ተሽከርካሪ አሁን አይገኝም።",

        # REVERSE_MATCH_STRINGS
        "scanning": "🔍 ንቁ የፈላጊ ጥያቄዎችን በማሰስ ላይ...",
        "no_matches": "✅ **ማስታወቂያው ወጥቷል!**\nበአሁኑ ጊዜ ይህንን አይነት በግልጽ የጠየቀ ፈላጊ የለም። አዲስ ሲኖር በራስ-ሰር ይነገርዎታል።",
        "top_seekers": "🎯 **ከፍተኛ {count} ፈላጊዎች ተገኝተዋል!**\nእነዚህ ተጠቃሚዎች የእርስዎን ንብረት የሚመጥን አገልግሎት በንቃት እየፈለጉ ነው። ጥያቄዎን "
                       "ለመላክ ከታች ይምረጡ:",
        "btn_match": "👤 #{index} ፈላጊ (ባጀት: {budget} {currency} - {score}%)",
        "btn_skip": "⏭️ ይለፉ እና ወደ ዋና ማውጫ ይመለሱ",
        "error_skipped": "✅ ማስታወቂያው በተሳካ ሁኔታ ተፈጥሯል። (በስርዓቱ ጫና ምክንያት ፍለጋው አልፏል)።",
        "MSG_TOP_SEEKER_MATCHES": "🎯 **ከፍተኛ {count} የ{vertical} ፈላጊዎች ተገኝተዋል!**\n\nእነዚህ ተጠቃሚዎች የእርስዎን ንብረት የሚመጥን "
                                  "አገልግሎት በንቃት እየፈለጉ ነው። ጥያቄዎን ለመላክ ከታች ይምረጡ:",
        "BTN_SEEKER_MATCH_ITEM": "👤 #{index} ፈላጊ (ባጀት: {budget} {currency} - {score}%)",

        # ==========================================
        # DEALS HANDLER
        # ==========================================
        "prompt_enter_counter_price": "እባክዎ መደራደሪያ ዋጋዎን ያስገቡ፡",
        "btn_cancel_counter": "❌ ድርድሩን ሰርዝ",
        "err_action_required_counter": (
            "⚠️ **እርምጃ ያስፈልጋል**\n\n"
            "የዋጋ ማሻሻያ (ድርድር) ለማስገባት እየሞከሩ ይመስላል። "
            "እባክዎ በመጀመሪያ በስምምነት ማሳወቂያ ካርዱ ላይ ያለውን **'📉 የድርድር ዋጋ'** የሚለውን ቁልፍ በቀጥታ ይጫኑ፣ "
            "ከዚያ ሲጠየቁ ዋጋዎን ይተይቡ።"
        ),
        "err_sys_general": "⚠️ የሲስተም ስህተት አጋጥሟል። እባክዎ እንደገና ይሞክሩ።",

        "error_system_state": "⚠️ የሲስተም ስህተት፡ የድርድር መረጃ ጠፍቷል። እባክዎ እንደገና ይጀምሩ።",
        "error_invalid_numeric_input": "⚠️ የተሳሳተ መረጃ። እባክዎ ትክክለኛ የዋጋ መጠን ያስገቡ።",
        "error_deal_expired": "⚠️ ይህ ድርድር ጊዜው አልፎበታል ወይም የድርድር ጊዜው ተጠናቋል።",
        "notice_provider_countered": "🔔 አቅራቢው አዲስ ዋጋ አቅርቧል። አዲሱ ዋጋ፡ *{price} ብር*",
        "notice_seeker_countered": "🔔 ገዢው አዲስ ዋጋ አቅርቧል። አዲሱ ዋጋ፡ *{price} ብር*",
        "btn_counter_offer": "💬 የድርድር ዋጋ አቅርብ",
        "btn_decline_deal": "🚫 ድርድሩን ውድቅ አድርግ",
        "success_counter_transmitted": "✅ የድርድር ዋጋዎ አቀራረብ *{price} ብር* በተሳካ ሁኔታ ተላክሏል።",
        "error_unexpected": "⚠️ ያልተጠበቀ ስህተት ተከሰተ። እባክዎ እንደገና ይሞክሩ።",
        "notice_counter_aborted": "❌ የዋጋ ማሻሻያው ተሰርዟል። ድርድሩ በቀደመው ዋጋ ይቀጥላል።",
        "notice_action_cancelled": "❌ ድርጊቱ በተሳካ ሁኔታ ተሰርዟል።",
        "error_transaction_invalid": "⚠️ የተሳሳተ የግብይት ጥያቄ። ድርድሩ ተዘግቶ ሊሆን ይችላል።",
        "error_execution": "⚠️ የተጠየቀውን ተግባር ማከናወን አልተቻለም። እባክዎ ድጋፍ ሰጪን ያነጋግሩ።",
        "btn_pay_commission": "💳 ኮሚሽን ይክፈሉ",
        "notice_deal_declined_self": "🚫 ድርድሩን ውድቅ አድርገዋል። ድርድሩ አሁን ተዘግቷል።",
        "notice_deal_declined_opposing": "🚫 ሌላኛው ወገን የቀረበውን ዋጋ ውድቅ አድርጎታል። ይህ ድርድር ተዘግቷል።",
        "msg_missed_alerts": "🔔 **በሌላ ስራ ላይ ሳሉ የሚከተሉት ማሳወቂያዎች ደርሰውዎታል፡**",
        "lbl_queued_notification": "🔔 ማሳወቂያ",
        "msg_queue_cleared_summary": "✅ **ሁሉም የሚጠባበቁ ማሳወቂያዎች ደርሰዋል።**",
        "err_queue_delivery_failed": "⚠️ *የተሰበሰበ ማሳወቂያ ማድረስ አልተቻለም። እባክዎ ገፁን ያድሱ።*",
        "msg_queue_overflow": "⚠️ **ማሳሰቢያ፡** የቆዩ ማሳወቂያዎች የገቢ መልዕክት ሳጥንዎን ለማቃለል ተሰርዘዋል።",

        "error_billing_pipeline": "⚠️ የክፍያ አገልግሎት አልሰራም።",
        "msg_negotiation_cancelled": "❌ ድርጊቱ ተሰርዟል።",
        "err_unauthorized_action": "⚠️ ያልተፈቀደ ድርጊት። እርሶ የዚህ ድርድር አካል አይደሉም።",
        "err_sys_invalid_price": "⚠️ የሲስተም ስህተት፡ የተሳሳተ የድርድር ዋጋ። እባክዎ ድጋፍ ሰጪን ያነጋግሩ።",

        # --- Notification Templates ---
        "notice_new_offer_template": (
            "🔔 *አዲስ የ{tx_type} ማቅረቢያ ({vertical_type})!*\n\n"
            "🏷️ *ግብይት:* {tx_type}\n"
            "🏠 *ንብረት:* _{asset_title}_\n"
            "{attributes}"
            "💰 *የቀረበ ዋጋ:* *{price} {currency}*"
        ),

        "notice_offer_sent_seeker": "✅ የ *{price} {currency}* ማቅረቢያዎ ለአቅራቢው ተልኳል!",
        "notice_reverse_offer_sent_provider": "✅ የ *{price} {currency}* ማቅረቢያዎ ለፈላጊው ተልኳል!",

        # --- Localized Terms ---
        "verticals": {
            "housing": "ቤት",
            "vehicle": "ተሽከርካሪ",
            "equipment": "መሳሪያዎች",
            "freight": "ዕቃ ማጓጓዣ",
            "ride": "ራይድ",
            "general": "ንብረት"
        },
        "transactions": {
            "rental": "ኪራይ",
            "rent": "ኪራይ",
            "sale": "ሽያጭ",
            "lease": "ሊዝ",
            "service": "አገልግሎት",
            "booking": "ቦታ ማስያዝ"
        },

        "msg_counter_prompt": "እባክዎ የመደራደሪያ ዋጋዎን ያስገቡ፡",
        "msg_counter_sent": "✅ የመደራደሪያ ዋጋ {price} {currency} በተሳካ ሁኔታ ተልኳል!",
        "notice_reverse_offer_sent_seeker": "✅ የ *{price} {currency}* ማቅረቢያዎ ለፈላጊው ተልኳል!",
        "notice_reverse_offer_template": (
            "🔄 *አዲስ የተገላቢጦሽ ማዛመጃ አቅርቦት ({vertical_type})!*\n"
            "አቅራቢው በአገልግሎት ጥያቄዎ ላይ ዋጋ አቅርቧል:\n"
            "🏷️ *ግብይት:* {tx_type}\n"
            "🏠 *ንብረት:* _{asset_title}_\n"
            "{attributes}\n"
            "💰 *የቀረበ ዋጋ:* *{price} {currency}*"
        ),

        "deal_agreed_provider_counter_accepted": "✅ ለ _{asset_title}_ የ *{price} {currency}* ተቃራኒ ዋጋ ተቀብለዋል። የፈላጊው "
                                                 "የኮሚሽን ክፍያ በመጠባበቅ ላይ።\n{details}",
        "deal_agreed_seeker_counter_accepted": "🎉 *የቀረበው ማሻሻያ ተቀባይነት አግኝቷል!*\n\nአቅራቢው ለ _{asset_title}_ ያቀረቡትን የ *{"
                                               "price} {currency}* ተቃራኒ ዋጋ ተቀብሏል።\n{details}\nዝርዝር መረጃ እና አድራሻ (ስልክ "
                                               "ቁጥር) ለማግኘት፣ እባክዎ ከታች ያለውን ኮሚሽን ይክፈሉ።",
        "deal_agreed_seeker_offer_accepted": "✅ *ማቅረቢያ ተቀባይነት አግኝቷል!*\n\nለ _{asset_title}_ የቀረበውን የ *{price} {"
                                             "currency}* ማቅረቢያ ተቀብለዋል።\n{details}\nዝርዝር መረጃ እና አድራሻ (ስልክ ቁጥር) ለማግኘት፣ "
                                             "እባክዎ ከታች ያለውን ኮሚሽን ይክፈሉ።",
        "deal_agreed_provider_offer_accepted": "🎉 *ማቅረቢያ ተቀባይነት አግኝቷል!*\n\nፈላጊው ለ _{asset_title}_ ያቀረቡትን የ *{price} "
                                               "{currency}* ማቅረቢያ ተቀብሏል።\n{details}\nየፈላጊውን የኮሚሽን ክፍያ በመጠባበቅ ላይ።",

        # ====================================================================
        # --- VERIFICATION (verify_) ---
        # ====================================================================
        "verify_prompt": "🛡️ ፕላትፎርማችንን ደህንነቱ የተጠበቀ ለማድረግ፣ እባክዎ የብሔራዊ መታወቂያዎን ወይም የመንጃ ፈቃድዎን ግልጽ ፎቶ ይላኩ።",
        "verify_pending": "⏳ መታወቂያዎ በ AI እየተረጋገጠ ነው። እባክዎ ይwait...",
        "verify_success": "✅ ማረጋገጫው ተሳክቷል! መለያዎ አሁን የታመነ ነው።",
        "verify_failed": "❌ ማረጋገጫው አልተሳካም። እባክዎ ምስሉ ግልጽ መሆኑን አረጋግጠው እንደገና ይሞክሩ።",
        "verify_start_message": "🛡️ **የስማርት ማች አፍሪካ ማረጋገጫ**\n\nየፕላትፎርሙን ደህንነት ለመጠበቅ፣ ማጭበርበርን ለመከላከል እና የጭነት/የተጓዥ "
                                "ትራንስፖርት አገልግሎትን ለመክፈት ማንነትዎን ማረጋገጥ ያስፈልጋል።\n\nእባክዎ ጥራት ያለው **የመንጃ ፈቃድ ወይም የብሔራዊ "
                                "መታወቂያ ፎቶ** ይላኩ።",
        "error_invalid_input_photo_required": "⚠️ **የተሳሳተ የግብአት አይነት**\n\nእባክዎ የማያያዣውን ምልክት (📎) በመጫን የመታወቂያዎን ግልጽ "
                                              "*ፎቶ* ይላኩ።",
        "verify_processing_document": "🔒 **ሰነዱ ደርሶናል!**\nወደ ደህንነቱ የተጠበቀ ስርዓት በመጫን ላይ እና በ AI እየተመረመረ ነው። እባክዎ "
                                      "ይwait...",
        "verify_upload_failed": "❌ ስርዓቱ ስራ በዝቶበታል። እባክዎ ቆየት ብለው በ /verify እንደገና ይሞክሩ።",
        "verify_success_message": "✅ **ማረጋገጫው በተሳካ ሁኔታ ተጠናቋል!**\n\nየአሽከርካሪ መገለጫዎ አሁን ገቢራዊ ሆኗል፣ ሙሉ በሙሉ ተረጋግጧል እንዲሁም "
                                  "በስማርት ማች አፍሪካ የታመነ ሆኗል።",
        "verify_failed_validation": "❌ **ማረጋገጫው አልተሳካም**\n\nየተላከው ሰነድ ግልጽ አልነበረም ወይም መስፈርቱን አላሟላም።\n\nእባክዎ /verify "
                                    "በማለት ጥራት ያለው ፎቶ እንደገና ይላኩ።",
        "error_system_timeout": "⚠️ **የስርዓት መዘግየት**\n\nከማረጋገጫ ስርዓቱ ጋር በመገናኘት ላይ ጊዜ አልፎበታል። እባክዎ ቆየት ብለው በ /verify "
                                "እንደገና ይሞክሩ።",
        "verify_cancelled_message": "🛑 **ማረጋገጫው ተቋርጧል**\n\nከማረጋገጫ ሂደቱ ወጥተዋል። በማንኛውም ጊዜ /verify በማለት መመለስ ይችላሉ።",

        # -- webhook_handler.py translations for dispatch section
        "dispatch_payment_confirmed": "✅ <b>ክፍያው ተረጋግጧል!</b>",
        "dispatch_seeker_body": "ለ <b>{asset_title}</b> (Deal ID: <code>{deal_id}</code>) ያደረጉት ግብይት አሁን ተጠናቋል።",
        "dispatch_match_finalized": "✅ <b>ስምምነቱ ተጠናቋል!</b>",
        "dispatch_provider_body": "ተጠቃሚው ለምዝገባዎ የፕላትፎርም ኮሚሽን ከፍሏል፦ <b>{asset_title}</b> (Deal ID: <code>{"
                                  "deal_id}</code>)።",
        "dispatch_category": "📂 <b>ምድብ:</b> <code>{vertical_name}</code>",
        "dispatch_transaction": "🏷️ <b>ግብይት:</b> <code>{transaction_type}</code>\n",
        "dispatch_asset_details": "🚙 <b>የንብረት ዝርዝር:</b> <code>{mmy}</code>",
        "dispatch_location": "📍 <b>አድራሻ:</b> <code>{location}</code>",
        "dispatch_provider_phone": "📞 <b>የአቅራቢ ስልክ:</b> <code>{phone}</code>",
        "dispatch_client_phone": "📞 <b>የደንበኛ ስልክ:</b> <code>{phone}</code>",
        "not_specified": "ልዩ መግለጫ አልተሰጠም",
        "person_unit": "ሰው",
        "people_unit": "ሰው",

        "dispatch_route": "📍 <b>መስመር:</b> <code>{origin} ➡️ {destination}</code>\n",
        "dispatch_cargo_weight": "⚖️ <b>የጭነት ክብደት:</b> <code>{weight} ኪ.ግ</code>\n",
        "dispatch_plate_number": "🚙 <b>የሰሌዳ ቁጥር:</b> <code>{plate}</code>\n",
        "dispatch_party_size": "👥 <b>የተሳፋሪዎች ብዛት:</b> <code>{party}</code>\n",
        "lbl_not_specified": "ልዩ መግለጫ አልተሰጠም",
        "dispatch_seeker_footer": "አገልግሎቱን ለማስጀመር ከአቅራቢው ጋር በቀጥታ ይገናኙ። ስማርት ማች አፍሪካን ስለተጠቀሙ እናመሰግናለን!",
        "dispatch_provider_footer": "ጥሪውን በቅርቡ ይጠብቁ፣ ወይም ለመጀመር በቀጥታ ያግኟቸው። Smart Match Africa ስለተጠቀሙ እናመሰግናለን!",

        # Missing Keys Added
        "LABEL_LOGISTICS_PROFILE": "የሎጂስቲክስ ፕሮፋይል",
        "LABEL_ESTIMATED_FARE": "የግምት ታሪፍ",
        "PROMPT_ENTER_OFFER_AMOUNT": "እባክዎ የራስዎን የዋጋ አቅርቦት ያስገቡ፦"
    },

    "om": {
        # =====================================
        # --- SYSTEM & ERRORS (sys_) ---
        # =====================================
        "sys_error_generic": "⚠️ Muka'insa qunnameera. Maaloo irra deebi'ii yaali ykn deeggarsa qunnami.",
        "sys_invalid_number": "⚠️ Galtee dogoggoraa. Maaloo lakkoofsa qofa galchi (fkn. 1500):",
        "sys_invalid_text": "⚠️ Galtee dogoggoraa. Maaloo barruu sirrii galchi.",
        "sys_cancelled": "🚫 Gochi haqameera. Gara tarree isa duraatti deebi'aa jira.",
        "sys_loading": "⏳ Gaaffii keessan raawwachaa jirra...",
        "sys_photo_prompt": "📸 Maaloo suuraa qulqulluu ol fe'i, ykn 'Skip' kan jedhu tuqi.",

        # ========================================
        # --- COMMON BUTTONS (btn_) ---
        # ========================================
        "btn_yes": "Eeyyee (Yes)",
        "btn_no": "Lakkii (No)",
        "btn_cancel": "❌ Haqi (Cancel)",
        "btn_confirm": "✅ Mirkaneessi (Confirm)",
        "btn_back": "⬅️ Deebi'i (Back)",

        # =====================================================================
        # MAIN MENU & WELCOME MESSAGE
        # =====================================================================
        "welcome_back": (
            "⚡ *BAGA GARA SMART MATCH AFRICA NAGAAN DHUFTAAN!* ⚡\n\n"
            "> *Iddoo Gabaa Qabeenyaa fi Geejjiba keessan isa battalaa*\n\n"
            "Kiiboordiin dhaabbataa keenya gadi irratti hidhameera.\n\n"
            "*Tajaajilawwan Ijoo:*\n"
            "• 🏠 *Mana:* Mana jireenyaa fi daldalaa galmeessuuf ykn barbaaduuf\n"
            "• 🚘 *Konkolaataa:* Konkolaataa dhuunfaa fi daldalaa dhiheessuuf ykn barbaaduuf\n"
            "• 🚜 *Meeshaalee:* Maashinaroota gurguddoo fi meeshaalee qonnaa\n"
            "• 📦 *Loojistiksii:* Fe'umsa fi imala magaalaa\n\n"
            "👇 *Jalqabuuf filannoowwan gadii irraa filadhaa:*"
        ),
        "menu_unrecognized": (
            "⚠️ *Ajaja Hin Beekamne*\n\n"
            "Maaloo filannoowwan kiiboordii gadii irra jiran fayyadamaa."
        ),
        # ----------------------------------------------------
        # MAIN MENU BUTTON LABELS (2x4 Grid Matrix)
        # ----------------------------------------------------
        "btn_list_house": "🏠 MANA GALMEESSUU",
        "btn_find_house": "🔍 MANA BARBAADUU",
        "btn_list_vehicle": "🚘 KONKOLAATAA GALMEESSUU",
        "btn_find_vehicle": "🔎 KONKOLAATAA BARBAADUU",
        "btn_list_equipment": "🚜 MEESHAA GALMEESSUU",
        "btn_find_equipment": "🏗️ MEESHAA BARBAADUU",
        "btn_freight": "📦 FE'UMSA",
        "btn_ride": "🚗 IMALA",

        # ----------------------------------------------------
        # MENU ENTRY PROMPTS
        # ----------------------------------------------------
        "menu_freight": "🚛 **Lojistiiksii Fe'umsaa**\n\nKaffaltii fe'umsaa ykn tajaajila fe'umsaa kennuu filadhu:",
        "menu_ride": "🚕 **Tajaajila Imalaa**\n\nImaltoota imaluu barbaaddan wajjin ykn tajaajila geejibaa kennuu "
                     "filadhu:",
        "menu_list_house": "🏠 **Qabeenya Galmeessi**\n\nQabeenya keessan kireessuu ykn gurguruu barbaaddu?",
        "menu_find_house": "🔍 **Qabeenya Barbaadi**\n\nQabeenya kireeffachuu ykn bitachuu barbaaddu?",
        "menu_list_vehicle": "🚗 **Konkolaataa Galmeessi**\n\nKonkolaataa keessan gurguruu ykn kireessuu barbaaddu?",
        "menu_find_vehicle": "🔍 **Konkolaataa Barbaadi**\n\nKonkolaataa bitachuu ykn kireeffachuu barbaaddu?",

        "msg_choose_language_prompt": "🌐 *Baga gara Smart Match Africa nagaan dhufte!*\n\nMaaloo afaan keessan "
                                      "filadhaa:",
        "msg_choose_language_short": "🌐 Afaan Filadhu / Choose Language / ቋንቋ ይምረጡ:",
        "msg_lang_set_success": "✅ Afaan keessan gara Afaan Oromootti jijjiirameera.",

        # ================================================
        # --- EQUIPMENT RENTAL (equip_) ---
        # ================================================
        "menu_list_equip": "🏗️ **Meeshaa Kireessuu**\n\nRamaddii meeshaa ykn hojii keessani filadhaa:",
        "menu_find_equip": "🔍 **Meeshaa Kireeffachuu**\n\nGosa meeshaa hojii barbaaddan filadhaa:",
        "btn_cat_construction": "🚜 ijaarsa fi Qotiisa",
        "btn_cat_farming": "🌾 Qonnaa fi Jallisii",
        "btn_cat_generators": "⚡ Jeneraataroota fi Humna",
        "btn_cat_eventtech": "🎪 Daldala fi Qophiilee",

        "prompt_equip_profile": "📝 **Ibsa Meeshaa: {category}**\n\nMaaloo gosa meeshaa galchaa (fkn, (fkn, "
                                "Ekskaavaatara Hayidirooliikii, Pumpilee Bishaanii, Iskaafoldingii)):",
        "prompt_sourcing_req": "📋 **Ulaagaa Kireeffannaa: {category}**\n\nGosa meeshaa kam kireeffachuu barbaaddu?",
        "err_name_too_short": "⚠️ Maqaan meeshaa baay'ee gabaabaa dha. Maaloo ibsa qulqulluu galchaa.",

        "prompt_capacity_list": "⚙️ **Dandeettii fi Oomisha**\n\nHumni ykn oomishni *{equip_type}* Meeqaa? (Fkn: "
                                "Toonii 20, 500 kVA, 150 L/s):",
        "prompt_capacity_find": "⚙️ **Ulaagaa Humna Barbaadamu**\n\nHumni xiqqaan *{equip_type}* irraa barbaadamu "
                                "meeqa? (Hoo filannoo hin qabne 'Any' jedhaa):",

        "prompt_brand_list": "🏷️ **Oomishtuu fi Amala Teeknikaa**\n\nMaqaa oomishtuu fi amaloota teeknikaa sararaan "
                             "addaan baasaa (fkn, Caterpillar - GPS Guided):",
        "prompt_brand_find": "🏷️ **Oomishtuu Filatamaa**\n\nOomishtuu filatamaa ykn amala teeknikaa barbaaddan "
                             "qabduu? ((fkn, Caterpillar - GPS Guided, Hoo hin qabne 'None' jedhaa):",

        "prompt_location_list": "📍 **Bakka Meeshaa**\n\nBakka meeshaan jiru Telegram Location (📎) fayyadamuun "
                                "ergaa, ykn teessoo barruun galchaa:",
        "prompt_location_find": "📍 **Bakka Hojiitti Fe'amu**\n\nMeeshaan kun eessatti hojjeta? Bakka hojichaa "
                                "Telegram Location (📎) fayyadamuun ergaa, ykn teessoo barruun galchaa:",
        "gps_coords_provided": "🌐 Qabxiileen GPS galmeeffamaniiru",

        "prompt_rate_list": "💰 **Gatii Sa'aatiin (ETB/USD)**\n\nMaaloo gatii kireessaa sa'aatii gaafattani gosa "
                            "maallakaa ifatti adda baasuun ETB ykn USD dhaan galchaa:",
        "prompt_rate_find": "💰 Daangaa Baajataa (ETB/USD)\n\nKaffaltii sa'aatii ol'aanaa fudhatama qabu gosa "
                            "maallaqaa ifatti adda baasuun ETB ykn USD dhaan galchaa:",
        "err_invalid_rate": "⚠️ Galtee dogoggoraa. Maaloo gatii lakkoofsaan galchaa.",
        "err_invalid_rate_system": "❌ Dogoggorri sirnaa gatii mirkaneessuu irratti uumameera. Maaloo irra deebi'a.",

        "prompt_condition_list": "🛠️ **Haala Meeshaa**\n\nHaala meeshaa ammaa filadhaa:",
        "prompt_condition_find": "🛠️ **Haala Xiqqaa Barbaadamu**\n\nHaala meeshaa xiqqaa hojii keessaniif barbaadamu"
                                 " filadhaa:",
        "btn_cond_exc": "🌟 Baay'ee Gaarii / Akka Haaraa",
        "btn_cond_good": "✅ Gaarii / Hojiif Qophii",
        "btn_cond_fair": "⚠️ Giddu-galeessa / Ni Hojjeta",

        "prompt_photo_upload": "📸 **Suuraa Meeshaa**\n\nMaaloo suuraa meeshaa qulqulluu ol fe'i (📎). Suuraa malee "
                               "dabruuf 'Skip' jedhi.",
        "msg_compressing_image": "⏳ Suuraa xiqqeessuun faayila daataatti ol fe'aa jira...",
        "err_upload_timeout": "⚠️ Suuraa ol fe'uun yeroon itti darbeera. Barruu qofaan itti fufaa jira.",

        "prompt_contact_info": "📞 **Lakk. Bilbilaa Direct**\n\nMaaloo lakk. bilbilaa geggeessaa bakka hojii galchaa:",
        "msg_finalizing_packet": "⚙️ Daataa gurmeessuun sirnaatti ergaa jira...",
        "msg_asset_live": "✅ **Qabeenyi Galmaa'eera!**\n\nMeeshaan keessan (*{equip_type}*) ammata Smart Match "
                          "irratti argama.",
        "MSG_TOP_EQUIP_MATCHES": "🎯 **Walsimannaa meeshaa ol'aanaa {count} argamaniiru!**\n\nMaaloo gamaggamuufi "
                                 "dhiyessii erguuf qabeenya gadii irraa filadhaa:",

        "msg_scanning_nodes": "📡 Meeshaa ulaagaa keessaniin walgitamu barbaadaa jira...",
        "msg_request_broadcasted": "📢 **Gaaffiin Keessan Tamsa'eera**\n\nYoosuus walgitinsa hin arganne. Gaaffiin "
                                   "keessan galmaa'ee tamsa'eera.",
        "msg_machinery_secured": "🎯 **Meeshaan Walsimatu Argameera! (Qabxii: {score}%)**\n\n**Meeshaa:** {brand} {"
                                 "equip_type}\n**Bakka:** {location}\n**Gatii:** {rate} ETB\n\nMeeshaa kana "
                                 "qabachuufi adeemsa kaffaltii koomishinii eegaluu barbaadduu?",
        "btn_rent_secure": "🔒 Meeshaa Qabadhu & Kaffali",
        "btn_decline": "❌ Hin Barbaadu",
        "err_system_anomaly": "❌ Dogoggorri keessoo uumameera. Maaloo irra deebi'ii eegali.",
        "err_request_not_found": "⚠️ Gaaffiin filatame amma hojiirra hin jiru.",

        "msg_initiating_gateway": "💳 Karra kaffaltii saaqaa jira...",
        "msg_asset_link_secured": "🧾 **Invooyisii Qophaa'eera**\n\nMaaloo kaffaltii raawwachuun odeeffannoo "
                                  "qunnamsiisaa argadhaa.",
        "btn_open_portal": "🌐 Fuula Kaffaltii Saaqi",
        "btn_verify_payment": "🔄 Kaffaltii Mirkaneessi",
        "err_gateway_request": "❌ Karra kaffaltii qunnamuu hin dandeenye.",
        "msg_escrow_cleared": "✅ **Kaffaltiin Mirkanaa'ee Meeshaan Qabameera!**\n\n**Qunnamsiisa:** {provider_contact}",
        "msg_payment_pending": "⏳ Kaffaltiin cufamee hin xumuramne. Maaloo kaffaltii xumuuraa.",

        "equip_offer_prompt": "🤝 **Gatii Dhiyeessi**\n\nMaaloo gatii dhiyeessitanii ETB tiin galchaa:",
        "btn_cancel_offer": "🚫 Dhiyeessii Haqi",
        "err_listing_unavailable": "❌ Meeshaan kun ammata hin argamu.",
        "err_offer_own_listing": "⚠️ Meeshaa keessan irratti gatii dhiyeessuu hin dandeessan.",
        "err_offer_failed": "❌ Haala waliigaltee eegaluun hin danda'amne.",
        "msg_process_cancelled": "🛑 Adeemsi meeshaa kireessuu dhaabbateera.",

        # Match Selection & Asset Card
        "msg_no_matches_found": "⚠️ Amma yeroo ammaa walitti dhufeenyi hojii irra jiru hin argamne. Gara menuu "
                                "deebi'aa.",
        "err_asset_not_found": "⚠️ Meeshaan filatame ammaan tana hojii irra hin jiru yookiin haqameera.",
        "lbl_equipment_selected": "🚜 <b>Meeshaa Filatame:</b> {asset_title}",
        "lbl_rental_rate": "💰 <b>Gatii Kireessaa:</b> <code>{price:,.2f} ETB</code>",
        "lbl_capacity_output": "⚡ <b>Humna/Oomisha:</b> <code>{capacity}</code>",
        "prompt_select_option": "Gatii yaada dhiyeessii (proposal) keessan galchuuf filannoowwan armaan gadii keessaa"
                                " tokko filadhaa:",

        # Custom Offer Prompt & Processing
        "prompt_custom_offer": (
            "💬 <b>Gatii Dhiyeessitan Galchaa</b>\n\n"
            "🚜 Meeshaa: <i>{model_name}</i>\n"
            "📍 Bakka: <b>{location_text}</b>\n"
            "💰 Gatii Gaafatame: <code>{asking_price:,.2f} ETB</code>\n\n"
            "Maaloo gatii dhiyeessitan qarshii Itoophiyaatiin lakkoofsa qofaaniin deebisaa (Fkn: <code>5000</code>):"
        ),

        # Transmission & Deal Locking Status
        "msg_offer_transmitted": (
            "✅ <b>Gatiin Keessan Ergameera!</b>\n\n"
            "Gatiin keessan <b>{formatted_price} ETB</b> <i>{safe_asset_title}</i>f abbaa qabeenyaaf karaa nagaatiin "
            "ergameera."
        ),
        "msg_equipment_selected": "🚜 <b>Meeshaa Filatame:</b>",
        "msg_property_use": "🛠️ <b>Gosa Meeshaalee:</b>",
        "msg_rental_rate": "💰 <b>Gatii Kiraa:</b>",
        "msg_capacity_output": "⚡ <b>Dandeettii/Bu'aa:</b>",
        "msg_select_offer_option_equip": "Dhiyeessii kee abbaa qabeenyaaf dhiyeessuuf filannoo armaan gadii keessaa "
                                         "tokko filadhaa:",
        "msg_location": "📍 <b>Bakka::</b>",
        "err_self_offer": "⚠️ Qabeenya keessan irratti gatii dhiheessuu hin dandeessan.",
        "msg_select_top_match": "🚜 <b>Filannoo Meeshaalee Wal Simatan:</b>\n\nMammachuuwwan armaan gadii keessaa "
                                "tokko filadhaa:",
        "msg_select_transport_match": "🚚 <b>Filannoowwan Geejjibaa Wal Simatan:</b>\n\nMaaloo filannoowwan armaa "
                                      "gadii keessaa tokko filadhaa:",
        "msg_select_housing_match": "🏠 <b>Filannoowwan Mana Wal Simatan:</b>\n\nMaaloo filannoowwan armaa gadii "
                                    "keessaa tokko filadhaa:",
        "BTN_CANCEL": "❌ Barbaacha Haqi",
        "msg_scanning_seekers": "Barbaaddota ulaagaa keessaniin walgitamu sakatta'aa jira...",
        "BTN_SKIP": "Cehi gara Tarree Guriitti Deebi'i",
        "msg_top_seekers_found": "Barbaaddonni ulaagaa keessaniin walgitamu argamaniiru!",
        "msg_seeker_request_selected_equip": "Gaaffiin barbaadaa milkaa'inaan filatameera. Walitti fiduun hojjetamaa "
                                             "jira...",
        "msg_no_seeker_matches": "Yeroo ammaa kanatti Barbaaddonni ulaagaa keessaniin walgitamu hin argamne, "
                                 "akkuma nu gaheen daddafnee isin beksisna.",
        "dispatch_reverse_seeker_body": (
            "Komishiniin pilaatfoormii gaaffii keetiif milkaa'inaan kaffalameera: <b>{asset_title}</b> "
            "(Adda baasituu Waliigaltee: <code>{deal_id}</code>).\nGatiin walii galame: <b>{price} {currency}</b>"
        ),
        "dispatch_reverse_provider_body": (
            "Barbaadaan qabeenyaa gaaffii walitti dhufeef komishinii kaffaleera: <b>{asset_title}</b> "
            "(Adda baasituu Waliigaltee: <code>{deal_id}</code>).\nGatiin walii galame: <b>{price} {currency}</b>"
        ),
        "dispatch_reverse_match_seeker_header": "✅ <b>Walitti-dhufeenyi Xumurame! (Walitti-dhufeenya Garagalfama)</b>",
        "dispatch_reverse_match_provider_header": "✅ <b>Walitti-dhufeenyi Xumurame! (Walitti-dhufeenya "
                                                  "Garagalfama)</b>",

        # ===============================================
        # --- HOUSING (house_) ---
        # ===============================================
        "msg_return_hub": "Gara tarree isa duraatti deebi'aa jira...",
        "msg_return_root": "Deebii barbaachisu eegaa jirra; maaloo obsaan eegaa, yookiin gara hojii biraatti "
                           "jijjiiruuf meenuu idilee fayyadamaa yookiin /start ergaa.",
        "btn_return_main": "🔙 Tarree Isa Duraa",
        "msg_list_house_prompt": "🏡 **Qabeenya Galmeessi**\n\nQabeenya keessan kireessuu ykn gurguruu barbaaddu?",
        "btn_rent_out": "🔑 Kireessuu",
        "btn_sell": "💰 Gurguruu",
        "msg_find_house_prompt": "🔍 **Qabeenya Barbaadi**\n\nQabeenya kireeffachuu ykn bitachuu barbaaddu?",
        "btn_rent_in": "🔑 Kireeffachuu",
        "btn_buy": "💰 Bitachuu",
        "msg_property_sector": "🏢 **Gosa Qabeenyaa**\n\nKun jireenyaaf moo daldalaaf?",
        "btn_residential": "🏠 Mana Jireenyaa",
        "btn_commercial": "🏢 Bakka Daldalaa",
        "btn_residential_plot": "Iddoo Lafa Jireenyaa",
        "btn_commercial_plot": "Iddoo Lafa Daldalaa",
        "msg_location_list": "📍 **Teessoo**\n\nMaaloo teessoo qabeenyichaa kaartaadhan (📎) ergaa ykn Maqaa "
                             "Magaalichaa, Kutaa Magaalaa/Aanaa/Ganda, fi Mandara keessan barreessaa.",
        "msg_location_find": "📍 **Teessoo Barbaadamu**\n\nEessaatti barbaaddu? Maaloo naannoo sana kaartaadhan (📎) "
                             "ergaa ykn Maqaa Magaalichaa, Kutaa Magaalaa/Aanaa/Ganda, fi Mandara keessan barreessaa.",
        "err_location_short": "⚠️ Teessoon galchitan baay'ee gabaabaa dha. Maaloo ibsa qulqulluu galchaa.",
        "msg_price_list": "💵 **Gatii**\n\nMaaloo gatii ittiin gurguruu ykn kireessuu barbaaddan akaakuu maallaqaa "
                          "wajjin galchaa (fkn, kireessaa ji'aatiif 25,000 ETB ykn 500 USD, ykn gurgurtaaf 1,500,"
                          "000 ETB ykn 30,000 USD).",
        "msg_budget_find": "💵 **Bajata Ol'aanaa**\n\nMaaloo baajata keessan isa ol'aanaa akaakuu maallaqaa wajjin "
                           "galchaa (fkn, kireessaa ji'aatiif 25,000 ETB ykn 500 USD, ykn gurgurtaaf 1,500,"
                           "000 ETB ykn 20,500 USD).",
        "err_invalid_number": "⚠️ Lakkoofsa dogoggoraa. Maaloo lakkoofsa qofa galchaa.",
        "msg_beds_list": "🛏️ **Kutaa Cisaa**\n\nKutaa cisaa meeqa qaba? (fkn. 3)",
        "msg_beds_find": "🛏️ **Kutaa Cisaa**\n\nKutaa cisaa xiqqaan meeqa barbaadu? (fkn. 2)",
        "msg_sqm_list": "📏 **Bal'ina (Sq Meter)**\n\nBal'inni iddoo sanaa meeqama? Lakkoofsa guutuu galchaa.",
        "msg_sqm_find": "📏 **Bal'ina (Sq Meter)**\n\nBal'inni xiqqaan barbaaddan meeqa?",
        "err_invalid_whole_number": "⚠️ Galtee dogoggoraa. Maaloo lakkoofsa guutuu galchaa.",
        "keyword_none": "hin jiru",
        "msg_amenities_list": "✨ **Tajaajilawwan Dabalataa**\n\nTajaajilawwan jiran koman addaan baasaa galchaa. ("
                              "fkn, Bakka Dhaabbannaa Konkolaataa, WiFi, Jenereetara, Bishaan Eeggannoo). Yoo "
                              "tajaajilli addaa hin jirre ta'e 'None' jedhaa barreessaa.",
        "msg_amenities_find": "✨ **Tajaajilawwan Barbaadaman**\n\nTajaajilawwan barbaaddan koman addaan baasaa "
                              "galchaa. (fkn, Bakka Dhaabbannaa Konkolaataa, WiFi, Jenereetara, Bishaan Eeggannoo). "
                              "Yoo tajaajilla addaa hin hinbarbaaddan ta'e 'None' jedhaa barreessaa.",
        "keyword_skip": "kuta",
        "msg_photo_prompt": "📸 **Suuraa Qabeenyaa**\n\nMaaloo suuraa qulqulluu ol fe'i. Suuraa malee dabruuf 'kuta' "
                            "jedhi.",
        "msg_uploading_b2": "⏳ Suuraa ol fe'aa jira...",
        "msg_photo_success": "✅ Suuraan milkaa'inaan ol fe'ameera!",
        "msg_photo_timeout": "⚠️ Suuraa ol fe'uun hin danda'amne. Suuraa malee itti fufaa jira.",
        "err_photo_invalid": "⚠️ Galtee dogoggoraa. Suuraa ol fe'a ykn 'kuta' jedha.",
        "msg_bulk_qty": "📦 Maaloo baay'ina dimshaashaa argamu galchaa:",
        "msg_contact_list": "📞 **Lakk. Bilbilaa**\n\nMaaloo lakk. bilbilaa keessan galchaa (fkn. 0911234567).",
        "msg_contact_find": "📞 **Lakk. Bilbilaa**\n\nMaaloo lakk. bilbilaa keessan galchaa (fkn. 0911234567).",
        "err_invalid_phone": "⚠️ Lakk. bilbilaa dogoggoraa. Maaloo lakk. bilbilaa sirrii galchaa.",
        "msg_processing_asset": "⚙️ Daataa keessan sirna wajjin walsimsiisaa jira...",
        "msg_listing_success": "🎉 **Galmaa'eera!**\n\nQabeenyi keessan milkaa'inaan galmaa'eera.",
        "msg_request_success": "📡 **Gaaffiin Keessan Galmaa'eera!**\n\nGaaffiin keessan galmaa'eera. Walsimannaa yoo "
                               "arganne isin beeksifna.",
        "err_system_db": "❌ Dogoggorri keessoo kuusaa daataa irratti uumameera.",
        "lbl_area_size": "Bal'ina Iddoo",
        "lbl_bedrooms": "Baay'ina kutaa ciisichaa: ",
        "lbl_property_selected": "Qabeenya Filatame",
        "lbl_asking_price": "💰 Gatii Gaafatame:",
        "lbl_select_option": "Gatii dhiyeessuuf filannoo gadii irraa filadhaa:",
        "BTN_ACCEPT_ASKING": "Gatii Gaafatame Fudhadhu",
        "housing_offer_title": "🔔 Gatii Manaa Haaraa Dhiyaate!",
        "objective": "Kaayyoo",
        "asset": "Qabeenya",
        "rental": "Kiraa",
        "sale": "Gurgurtaa",
        "residential": "Jireenyaa",
        "commercial": "Daldalaa",

        "offer_price": "Gatii Dhiyaate",
        "match_btn_format": "🏠 #{idx}: {prop_type} {location} keessatti ({price} ETB - {score}%)",

        "msg_scanning_housing_nodes": "🔍 Qabeenyawwan jiran barbaadaa jira...",
        "MSG_TOP_HOUSING_MATCHES": "🎯 <b>Qabeenyonni {count} ulaagaa keessaniin walgitan argamaniiru!</b>\n\nMaaloo "
                                   "filachuun gatii keessan ergaa:",
        "notice_new_offer_received": "🔔 <b>Gatii {vertical_type} Haaraa!</b>\n\n🏠 <b>Qabeenya:</b> {"
                                     "asset_title}\n💰 <b>Gatii:</b> <code>{price} ETB</code>",

        "BTN_SEND_OFFER": "🤝 Gatii Keessan Galchaa",
        "btn_accept_asking": "✅ Gatii gaafatame/dhiyaate fudhadha ({price} ETB)",

        "lbl_objective": "Kaayyoo:",
        "btn_accept_price": "✅ Gatii Fudhadhu",

        "msg_seeker_request_selected_housing": "👤 <b>Gaaffiin Barbaadaa Filatameera:</b>",
        "prompt_enter_offer": (
            "💬 <b>Gatii Dhiyeessitan Galchaa</b>\n\n"
            "Qabeenya: <i>{asset_title}</i>\n"
            "Gatii Gaafatame: <code>{price} ETB</code>\n\n"
            "Maaloo gatii dhiyeessitan lakkoofsaan galchaa (fkn. <code>22000</code>):"
        ),
        "err_deal_blocked_conflict": "⚠️ **Waliigalteen Dhorkameera**\n\nYeroon keessan dhumateera ykn fayyadamaan "
                                     "biraa itti fayyadamaa jira.",
        "err_deal_expired": "⚠️ Waliigalteen kun yeroon itti darbeera.",
        "err_target_unavailable": "⚠️ Gatii dhiyeessuu hin danda'amne. Tangoon hin argamu.",
        "err_unexpected": "⚠️ Dogoggorri hin eegamne uumameera.",
        "notice_counter_received": "🔄 **Odeeffannoo Haaraa Marii Furiinsaa**\n\n📂 **Gosa:** {vertical_type}\n🏠 "
                                   "**Qabeenya:** {asset_title}\n👤 **Irraa:** {source_actor}\n💰 **Gatii Haaraa:** "
                                   "**{price} ETB**",
        "role_provider": "Dhiyeessaa",
        "role_seeker": "Barbaadaa",
        "lbl_default_asset": "Tarree Qabeenyaa",
        "msg_match_cancelled": "❌ Barbaachi qabeenyaa haqameera.",

        "alert_search_cancelled": "Barbaachi haqameera.",
        "err_verify_provider": "⚠️ Dhiyeessaa qabeenyaa mirkaneessuu hin danda'amne.",
        "msg_transmitting_offer": "🔄 Gatii dhiyaate dhiyeessaaf ergaa jira...",
        "msg_offer_sent": "✅ <b>Gatiin Milkaa'inaan Ergameera!</b>\n\nGatiin {transaction_type} <b>{price} ETB</b> "
                          "<b>{asset_title}</b>'f ergameera.",
        "err_deal_init_failed": "⚠️ Waliigaltee manaa eegaluun hin danda'amne.",

        "err_internal_pipeline": "⚠️ Adeemsa odeeffannoo sana keessatti dogoggorri uumameera. Maaloo booda irra "
                                 "deebi'ii yaali.",
        "err_property_not_found": "⚠️ Qabeenyi filatame ammata hin argamu.",
        "err_pending_negotiation": (
            "🚫 **Waliigalteen Dhorkameera**\n\n"
            "Qabeenyi kun ammata fayyadamaa biraa wajjin waliigaltee irratti argama."
        ),

        "btn_pay_chapa": "💳 Kaffaltii Koomishinii Chapa'n Kaffali",
        "msg_escrow_commission_fee": "🔐 **Odeeffannoo Abbaa Qabeenyaa**\n\nLakk. bilbilaa fi teessoo GPS argachuuf "
                                     "koomishinii **{amount} ETB** kaffalaa.",
        "err_chapa_init": "❌ Karra kaffaltii Chapa eegaluun hin danda'amne.",
        "err_chapa_system": "❌ Dogoggorri ol'aanaan karra kaffaltii irratti uumameera.",

        # =============================================
        # --- VEHICLES (veh_) ---
        # =============================================
        "LIST_VEHICLE": "🚗 Konkolaataa Baasuu",
        "FIND_VEHICLE": "🔍 Konkolaataa Barbaaduu",
        "vehicle.btn.return_main": "🏠 Gara Tarree Isa Duraatti Deebi'i",
        "vehicle.btn.back": "⬅️ Deebi'i",
        "vehicle.btn.cancel": "❌ Haqi",
        "vehicle.btn.submit": "✅ Ergi",
        "vehicle.offer.prompt": "🤝 **Gatii Dhiyeessi**\n\nMaaloo gatii keessan ETB tiin galchaa (lakkoofsa qofa).",
        "vehicle.btn.cancel_offer": "❌ Dhiyeessii Haqi",
        "vehicle.error.invalid_number": "⚠️ Lakkoofsa dogoggoraa. Maaloo lakkoofsa qofa galchaa.",
        "vehicle.error.listing_unavailable": "⚠️ Konkolaataan kun ammata hin argamu.",
        "vehicle.error.cannot_offer_own": "⚠️ Konkolaataa keessan irratti gatii dhiyeessuu hin dandeessan.",
        "vehicle.error.system_offline": "⚠️ Sirni waliigaltee ammata dhaabbateera.",
        "vehicle.offer.sent_success": "✅ Gaaffiin **{initial_offer} ETB** dhiyeessaaf ergameera.",
        "vehicle.offer.sent_fail": "❌ Gaaffii erguun hin danda'amne.",

        "vehicle.list.welcome": "🚗 **Konkolaataa Galmeessi**\n\nKonkolaataa gurguruu ykn kireessuu barbaaddu?",
        "vehicle.btn.sell_vehicle": "💰 Gurguruu",
        "vehicle.btn.rent_out": "🔑 Kireessuu",
        "vehicle.find.welcome": "🔍 **Konkolaataa Barbaadi**\n\nKonkolaataa bitachuu ykn kireeffachuu barbaaddu?",
        "vehicle.btn.buy_vehicle": "💰 Bitachuu",
        "vehicle.btn.rent_lease": "🔑 Kireeffachuu",

        "vehicle.vtype.title": "⚡ **SMART MATCH GOSA KONKOLAATAA** ⚡",
        "vehicle.vtype.sedan": "🚗 Sedan",
        "vehicle.vtype.suv": "🚙 SUV / SAV",
        "vehicle.vtype.van": "🚐 Vaanii (Van)",
        "vehicle.vtype.bus": "🚌 Baasii / Miiniibaasii",
        "vehicle.vtype.truck": "🚚 Konkolaataa Fe'umsaa (Truck)",
        "vehicle.vtype.moto": "🏍️ Moottorsayikilii",
        "vehicle.list.vtype_prompt": "Gosa konkolaataa galmeessuu barbaaddan filadhaa:",
        "vehicle.find.vtype_prompt": "Gosa konkolaataa argachuu barbaaddan filadhaa:",

        "vehicle.list.make_model_prompt": "🏷️ **Maqaa, Modeela fi Bara Oomishaa**\n\nMaaloo maqaa, modeela fi bara "
                                          "oomishaa galchaa (fkn. Toyota Doulfine Minibus 2LT 2000).",
        "vehicle.find.make_model_prompt": "🏷️ **Maqaa, Modeela fi Bara Oomishaa**\n\nMaaloo maqaa, modeela fi Bara "
                                          "Oomishaa galchaa. (fkn, Toyota Dolphine Minibus 2LT 2000).",
        "vehicle.error.input_too_short": "⚠️ Galteen baay'ee gabaabaa dha.",

        "vehicle.list.condition_prompt": "⏳ **Bara Tajaajile**\n\nWaggaa meeqa tajaajile? Haaraa yoo ta'e '0' galchaa.",
        "vehicle.find.condition_prompt": "⏳ **Bara Tajaajile Ol'aanaa**\n\nWaggaa meeqaa ol tajaajiluu hin qabu? "
                                         "Haaraa qofaaf '0' galchaa.",
        "vehicle.error.invalid_format_number": "⚠️ Akkaataa dogoggoraa. Maaloo lakkoofsa guutuu galchaa.",

        "vehicle.fuel.title": "⛽ **Gosa Boba'aa**",
        "vehicle.fuel.benzine": "⛽ Beenzinii (Beenzila)",
        "vehicle.fuel.diesel": "🛢️ Dizilii (Naafxaa)",
        "vehicle.fuel.electric": "⚡ Elektiriika",
        "vehicle.fuel.lng_ngv": "🌐 LNG / Gaazii Uumamaa (NGV)",
        "vehicle.fuel.hybrid_petrol": "🔋 Beenzinii-Elektiriika Haaybiriidii",
        "vehicle.fuel.hybrid_diesel": "🔋 Dizilii-Elektiriika Haaybiriidii",
        "vehicle.fuel.hybrid_flex": "🌱 Flex-Fuel (Etaanoolii) Haaybiriidii",
        "vehicle.fuel.hybrid_gaseous": "🔥 Haayibriidii Boba'aa Lameen (Gaasii-Elektiriikii)",
        "vehicle.fuel.hybrid_hydrogen": "💧 Haayidroojiinii Haaybiriidii",
        "vehicle.list.fuel_prompt": "Gosa boba'aa konkolaataa filadhaa:",
        "vehicle.find.fuel_prompt": "Gosa boba'aa barbaaddan filadhaa:",

        "vehicle.keyword.none": "hin jiru",
        "vehicle.list.features_prompt": "✨ **Amaloota Dabalataa**\n\nAmaloota dabalataa koman addaan baasaa galchaa  "
                                        "(fkn, AC, Tiraansimishinii Harkaa, Kaameraa Duubaa). Hin jiru yoo ta'e 'hin "
                                        "jiru' jedhaa.",
        "vehicle.find.features_prompt": "✨ **Amaloota Barbaadaman**\n\nAmaloota barbaadaman galchaa. \n(fkn, "
                                        "Tiraansimishinii Of-hojjataa, AC). Yoo ulaagaa dabalataa hin qabne ta'e 'hin"
                                        " jiru' jedhii barreessi.",

        "vehicle.list.location_prompt": "📍 **Teessoo**\n\nKonkolaataan eessa jira? Iddoo keessan ergaa (📎) ykn "
                                        "maqaa Biyya, magaalaa/ganda barreessaa.",
        "vehicle.find.location_prompt": "📍 **Teessoo Barbaadamu**\n\nEessatti barbaaddu? Iddoo keessan ergaa (📎) "
                                        "ykn maqaa Biyya, magaalaa/ganda barreessaa.",
        "vehicle.location.gps_pin": "Teessoon GPS Ergameera",

        "vehicle.list.price_sell_prompt": "💵 **Gatii Gurgurtaa**\n\nGatii gurgurtaa gosa maallaqaa ETB/USD tiin adda "
                                          "baasaa galchaa (fkn, 1,500,000 ETB, ykn 10,150 USD).",
        "vehicle.list.price_rent_prompt": "💵 **Gatii Kiraa Guyyaa**\n\nGatii kiraa guyyaa gosa maallaqaa ETB/USD "
                                          "tiin adda baasaa galchaa (fkn, 3,000 ETB, ykn 50 USD).",
        "vehicle.find.budget_buy_prompt": "💵 **Bajata Bitannaa Ol'aanaa**\n\nBajata bitannaa gosa maallaqaa ETB/USD "
                                          "tiin adda baasaa galchaa (fkn, 1,500,000 ETB, ykn 10,150 USD).",
        "vehicle.find.budget_rent_prompt": "💵 **Bajata Kiraa Guyyaa Ol'aanaa**\n\nBajata kiraa guyyaa gosa maallaqaa "
                                           "ETB/USD tiin adda baasaa galchaa (fkn, 3,000 ETB, ykn 50 USD).",
        "vehicle.error.invalid_price": "⚠️ Gatii dogoggoraa.",

        "vehicle.list.photo_prompt": "📸 **Suuraa Konkolaataa**\n\nMaaloo suuraa qulqulluu ol fe'i. Dabruuf 'kuta' "
                                     "jedhi.",
        "vehicle.photo.uploading": "⏳ Suuraa ol fe'aa jira...",
        "vehicle.photo.upload_timeout": "⚠️ Suuraa ol fe'uun hin danda'amne.",

        "vehicle.error.timeout": "⚠️ Yeroo Telegram waliin walqunnamtaan yeroon neetworkii dhumateera. Gaaffiin "
                                 "keessan olkaa'ameera; maaloo irra deebi'aatii yaalaa.",
        "vehicle.error.telegram_api": "⚠️ Dogoggorri walqunnamtii Telegram mudateera. Maaloo irra deebi'aatii yaalaa.",
        "vehicle_seeker_request_selected": "📋 <b>Gaaffii Barbaaddaa Filatame:</b>",
        "preferred_mmy": "🚘 <b>Akaakuu/Moodeela/Bara Filatame:</b>",
        "preferred_fuel": "⛽ <b>Gosa Boba'aa Filatame:</b>",
        "location_route": "📍 <b>Bakka / Karaa:</b>",
        "max_budget": "💰 <b>Baajata Olaanaa:</b>",
        "select_option_offer": "👇 <b>Dhiheessa kee dhiheessuuf filannoowwan gadii irraa filadhaa:</b>",
        'vehicle_selected': "✅ <b>Konkolaataan Filatameera</b>",
        'type': "📂 <b>Gosa:</b>",
        'listing_mmy': "🚗 <b>Gosa/Modeli/Waggaa Tarreeffame:</b>",
        'listing_fuel': "⛽ <b>Gosa Bobbaa Tarreeffame:</b>",
        'location': "📍 <b>Bakki:</b>",
        'price': "💰 <b>Gatii:</b>",

        # Status & System Alerts
        "msg_request_inactive": "⚠️ <b>Gaaffiin barbaadaa filatame amma hojiirra hin jiru.</b>",
        "msg_vehicle_inactive": "⚠️ <b>Makainni filatame amma hojiirra hin jiru.</b>",
        # Section Headers
        "seeker_match_details_header": "👤 TARREEFFAA MATCHII BARBAADAA",
        "vehicle_option_details_header": "🚗 TARREEFFAA FILANNOO MAKAINA",
        # Property & Field Labels
        "lbl_your_listed_vehicle": "Makaina keessan galmeeffame",
        "lbl_requested_spec": "🎯 Ispecifikeshinii Gaafatame:",
        "lbl_proposed_spec": "🚙 Ispecifikeshinii Dhiyeessitan:",
        "lbl_your_requested_spec": "🎯 Ispecifikeshinii Gaafattan:",
        "lbl_offered_vehicle_spec": "🚘 Ispecifikeshinii Makaina Dhiyaatee:",
        "lbl_fuel_type": "⛽ Gosa Boba'aa:",
        "lbl_your_vehicle": "Makaina Keessan",
        "lbl_location_route": "📍 Bakka/Karaa:",
        "lbl_location": "📍 Bakka:",
        "lbl_seeker_budget": "💰 Bajata Barbaadaa:",
        "lbl_flexible": "Salgali/Jijjiiramaa",
        # Action Prompts & Footers
        "match_flexibility_note": "💡 Yaadachiisa: Matchiin kun ispecifikeshiniin xiqqoo yoo adda ta'ellee makaina "
                                  "keessan dhiyeessuu nidandeessisa.",

        'select_option_owner': "Abbaa qabeenyaaf dhiyeessii keessan ergachuuf filannoo armaan gadii filadhaa:",
        'enter_custom_offer_btn': "🤝 Dhiyeessii keessan Galchaa",
        'send_asking_rate': "✅ Gatii Gaafatame Ergi ({price} {currency})",
        'accept_asking_rate': "✅ Gatii Gaafatame Fudhadi ({price} {currency})",
        'back_to_matches': "❌ Gara Walitti Dhiyeenyaatti Deebi'i",
        'decline_deal': "❌ Waliigaltee Diduuf",
        'prompt_offer_title': "💬 <b>Dhiyeessii Keessan Galchaa</b>",
        'prompt_seeker_offer_title': "💬 <b>Barbaadaa kanaaf Dhiyeessii Keessan Galchaa</b>",
        'seeker_request_label': "👤 Gaaffii:",
        'seeker_budget_label': "💰 Baajjetii Barbaadaa:",
        'asking_rate_label': "💰 Gatii Gaafatame:",
        'prompt_reply_instructions': "Maaloo {currency} dhaan qofa deebisaa (lakkoofsa qofa, fakkeenyaaf: `5000`):",
        'invalid_amount': "⚠️ <b>Fayyadama lakkoofsaa sirrii miti.</b>\nMaaloo lakkoofsa qofa galchaa.\n*("
                          "Fakkeenyaaf: 5000)*",
        'transmitting': "📡 <i>Dhiheessichi dabarfamaa jira, chaanilii daldalaa qopheeffamaa jira...</i>",
        'err_seeker_listing': "⚠️ <b>Dogoggora Sirnaa:</b> ID Barbaadaddaa ykn Tarreeffamaa dhabameera.",
        'err_provider': "⚠️ <b>Dogoggora Sirnaa:</b> Abbaa konkolaatichaa mirkaneessuu hin danda'amne.",
        'seeker_proposal_title': "📩 <b>DHIHEESSII KONKOLAATAA HAARAA ARGAMEERA</b>",
        'seeker_proposal_desc': "Abbaan konkolaataa gaaffii keessaniif gatii dhiheesseera!",
        'proposed_rate': "💰 <b>Gatii Dhiyaate:</b>",
        'requested_veh': "🎯 <b>Kan Gaafatame:</b>",
        'offered_veh': "🚗 <b>Kan Dhiyaate:</b>",
        'note_diff': "💡 <i>Hubachiisa: Bal'ina konkolaatichaa xiyyeeffannoon ilaalaa.</i>",

        'forward_offer_transmitted': "✅ <b>Dhiheessichi Dabarfameera!</b>\n\nGatiin <b>{formatted_price}</b> "
                                     "dhiheessite <i>{asset_title}</i>f milkaa'inaan ergameera.",
        'provider_offer_title': "📩 <b>KONKOLAATAA KEEF DHIHEESSII HAARAA QABA</b>",
        'provider_offer_desc': "Barbaadaddaan tokko tarreeffama konkolaataa keessaniif gatii dhiheesseera!",
        'offered_amount': "💰 <b>Gatii Dhiyaate:</b>",
        'listed_veh': "📝 <b>Kan Tarreeffame:</b>",
        'seeker_desired_veh': "🎯 <b>Barbaadaddaa Fedhe:</b>",

        'accept_btn': "✅ Fudhachuuf",
        'counter_btn': "💬 Gatii Biroo Dhiheessuuf",
        'decline_btn': "❌ Dhowwuu",

        'deal_blocked': "⚠️ <b>Chaanilii daldalaa jalqabuu hin danda'amne. Maaloo irra deebi'aa yaalaa.</b>",
        'system_err': "⚠️ <b>Dogoggorri sirnaa mudateera. Maaloo booda irra deebi'aa yaalaa.</b>",

        'new_provider_offer_title': "🔔 <b>Dhiyeessii Haaraa Dhaabbataa irraa Dhufe!</b>",
        'new_provider_offer_intro': "Dhiyeessaan gatii <b>{formatted_price}</b> dhiyeessera.",
        'requested_specs_header': "📌 <b>Gaaffii Keessan (Kan Barbaadame):</b>",
        'offered_specs_header': "🚗 <b>Gosa Dhiyeeffame:</b>",
        'spec_mmy': "  • Modeli/Waggaa: {val}",
        'spec_fuel': "  • Gosa Bobbaa: {val}",
        'choose_action': "Maaloo deebii kennuuf filannoo armaan gadii filadhaa:",
        'accept_price_btn': "✅ Gatii Fudhadi",
        'counter_offer_btn': "📉 Gatii Al-Lameen",
        "transmitting_offer": "🔄 <i>Dhiyeessichi ergamaa jira...</i>",
        "offer_transmitted": "✅ <b>Dhiheessaan Ergamaa Jira!</b>\n\nGatiin keessan <b>{formatted_price}</b> kan <i>{"
                             "asset_title}</i> tiif ergameera.",
        "provider_offer_transmitted": "✅ <b>Dhiheessaan Milkaa'inaan Ergameera!</b>\n\nGatiin keessan <code>{"
                                      "formatted_price}</code> kan <b>{list_mmy}</b> tiif barbaadahaaf ergameera.",
        'new_offer_on_listing_title': "🔔 <b>Tarreeffama Keessan Irratti Dhiyeessii Haaraa!</b>",
        'new_offer_on_listing_intro': "Barbaadaan tokko gatii <b>{formatted_price}</b> dhiyeessera.",
        'your_listing_specs_header': "🚗 <b>Tarreeffama Keessan:</b>",
        'seekers_requested_specs_header': "📌 <b>Gaaffii Barbaadaa:</b>",
        'accept_offer_btn': "✅ Dhiyeessii Fudhadi",
        'negotiation_blocked': "🚫 <b>Mariin Cufameera</b>\n\nQabeenyaan kun amma marii irra jira.",
        'system_error': "⚠️ Dogoggora Sirnaa: Mariin jalqabuu hin danda'amne.",
        "vehicle.list.contact_prompt": "📞 **Lakk. Bilbilaa**\n\nMaaloo lakk. bilbilaa keessan galchaa.",
        "vehicle.find.contact_prompt": "📞 **Lakk. Bilbilaa**\n\nMaaloo lakk. bilbilaa keessan galchaa.",
        "vehicle.sync.fleet_records": "⚙️ Daataa walsimsiisaa jira...",
        "vehicle.list.success": "🎉 **Galmaa'eera!**\n\nKonkolaataan keessan ({year_model}) galmaa'eera.",
        "vehicle.find.interrogating": "🔍 Konkolaataa walsimatu barbaadaa jira...",
        "vehicle.find.no_match": "📡 Gaaffiin keessan tamsa'eera. Walsimannaa yoo arganne isin beeksifna.",

        "MSG_TOP_VEHICLE_SEEKER_MATCHES": "🎯 <b>Barbaadtonni {vertical} Wooyyaanii {count} "
                                          "Argamaniiru!</b>\n──────────────────────────────\nBarbaadtonni kunniin "
                                          "konkolaataa barbaadaa jiru. Konkolaataa keessan dhiheessuuf gaaffilee "
                                          "barbaaddotaa gadii keessaa filadhaa:",
        # --- DOMAIN / VERTICAL NAMES ---
        "domain_housing": "Mana",
        "domain_vehicle": "Konkolaataa",
        "domain_equipment": "Meeshaa",

        "vehicle.btn.lock_vehicle": "🔐 Mirkaneessi & Kaffali",
        "vehicle.btn.decline_match": "❌ Hin Barbaadu",
        "vehicle.label.daily_rental": "Gatii Kiraa Guyyaa",
        "vehicle.label.purchase_price": "Gatii Gurgurtaa",
        "vehicle.find.match_found": "🎯 **Konkolaataan Argameera!** (Walsimannaa: {score}%)\n\n**Konkolaataa:** {"
                                    "asset}\n**Boba'aa:** {fuel}\n**Teessoo:** {location}\n**{price_label}:** {price} "
                                    "ETB\n\nKonkolaataa kana fudhachuu barbaaddu?",
        "vehicle.error.pipeline_crash": "❌ Dogoggorri keessoo uumameera.",
        "vehicle.escrow.syncing": "⚙️ Invooyisii qopheessaa jira...",
        "vehicle.btn.open_portal": "💳 Kaffaltii Raawwadhu",
        "vehicle.escrow.initialized": "🔐 **Kaffaltii**\n\nOdeeffannoo dhiyeessaa argachuuf koomishinii kaffalaa.",
        "vehicle.escrow.failed": "❌ Karra kaffaltii qopheessuun hin danda'amne.",
        "vehicle.cancel_message": "🛑 Adeemsi haqameera.",
        "vehicle.btn.send_offer": "🤝 Abbaa Qabeenyaaf Gatii Ergi",
        "vehicle.find.top_matches_found": "🎯 Walsimannaa gaarii argineera. Maaloo filadhaa:",
        "vehicle.negotiation.syncing": "🔄 Gaaffii abbaa konkolaataaf ergaa jira...",
        "vehicle.negotiation.started": "✅ **Gaaffiin Ergameera!**\n\nGaaffiin `{price} ETB` abbaa konkolaataaf "
                                       "ergameera.",

        # ==============================================
        # --- FREIGHT & RIDE (logistics_) ---
        # ==============================================
        "RETURN_MAIN_MENU": "Gara Tarree Isa Duraatti Deebi'aa jira...",
        "FREIGHT_OFFER_PROMPT": "🤝 **Gatii Dhiyeessi**\n\nMaaloo gatii keessan ETB tiin galchaa.",
        "BTN_CANCEL_OFFER": "❌ Haqi",
        "ERR_INVALID_NUMBER": "⚠️ Lakkoofsa dogoggoraa.",
        "ERR_LISTING_UNAVAILABLE": "⚠️ Odeeffannoon kun ammata hin argamu.",
        "ERR_OWN_LISTING": "⚠️ Qabeenya keessan irratti gatii dhiyeessuu hin dandeessan.",
        "ERR_SYS_NEGOTIATION_OFFLINE": "⚠️ Sirni waliigaltee ammata dhaabbateera.",
        "MSG_OFFER_SENT": "✅ Gaaffiin **{amount} ETB** ergameera.",
        "ERR_OFFER_FAILED": "❌ Gaaffii erguun hin danda'amne.",
        "MSG_PROCESS_CANCELLED": "🛑 Adeemsi haqameera.",

        "MSG_FREIGHT_START": "🚛 **Lojistiiksii Fe'umsaa**\n\nFe'umsa erguu barbaaddu moo tajaajila fe'umsaa kennuu?",
        "BTN_ROLE_SHIPPER": "📦 Konkolaataa Fe'umsaa Barbaadaa",
        "BTN_ROLE_CARRIER": "🚚 Fe'aa (Abbaa Konkolaataa)",

        "MSG_RIDE_START": "🚕 **Tajaajila Imalaa**\n\nImaltaa geejiba barbaadu moo ooftuu tajaajila kennu?",
        "BTN_ROLE_PASSENGER": "🧍 Barbaadaa tajaajila geejjibaa",
        "BTN_ROLE_DRIVER": "🚗 Konkolaachisaa (Tajaajila kennaa)",

        "PROMPT_ORIGIN_SHIPPER": "📍 **Bakka Ka'umsaa**\n\nFe'umsi ammata eessa jira?",
        "PROMPT_ORIGIN_CARRIER": "📍 **Bakka Ka'umsaa**\n\nKonkolaataan keessan yeroo ammaa eessa qubatee jira ykn "
                                 "eessaa hojjechaa jira?",
        "PROMPT_ORIGIN_PASSENGER": "📍 **Bakka Ka'umsaa**\n\nEessaarraa ka'u barbaaddu?",
        "PROMPT_ORIGIN_DRIVER": "📍 **Bakka Ammaa**\n\nAmmata eessa jirtu?",
        "ERR_VAGUE_ADDRESS": "⚠️ Teessoon galchitan baay'ee gabaabaa dha.",

        "PROMPT_DEST_SHIPPER": "📍 **Bakka Geessumaa**\n\n{origin} irraa ka'ee eessa gaha?",
        "PROMPT_DEST_CARRIER": "📍 Ka'umsi {origin} mirkanaa'eera. Maaloo bakka geessumaa galchaa:",
        "PROMPT_DEST_PASSENGER": "📍 **Bakka Geessumaa**\n\n{origin} irraa ka'ootii eessa deemtu?",
        "PROMPT_DEST_DRIVER": "📍 **Bakka Geessumaa**\n\n{origin} irraa ka'ootii eessa deemuufi?",
        "ERR_INVALID_DESTINATION": "⚠️ Teessoon geessumaa ibsamuu qaba.",
        "PROMPT_ENTER_OPERATING_REGION": "Maaloo naannoo hojii keessanii ykn naannoo hojii filattan galchaa. (fkn, "
                                         "Somaalee ykn Jijjigaa irraa hanga Finfinneetti)",
        "BTN_SHARE_GPS": "📍 Bakka GPS Keessan Eeraa",
        "MSG_GPS_LOCKED": "✅ Teessoon GPS milkaa'inaan galmeeffameera.",
        "MSG_GPS_BYPASSED": "⚠️ Teessoon GPS hin kennamne.  Teessoo barreeffama dhihaateen itti fufa.",
        "MSG_COORDS_LOCKED": "🛰️ Teessoon koordinetii mirkanaa'eera: **{lat:.6f}° N, {lon:.6f}° E**",
        "PROMPT_GPS_CALIBRATION": (
            "🛰️ **Malleessuu GPS**\n\n"
            "• **Fayyadamtoota Moobaayilaa:** Bakka keessan qoodachuuf qabduu gadii tuqaa.\n"
            "• **Kompuyutaraa / Harkaaniin:** Maqaa naannoo keessaniis ta'e teessoo koordinetii barreessaa\n"
            "  *(fkn., `Latitude 8.514477° N, Longitude 39.269257° E` ykn `8° 30' 52\" N, 39° 16' 9\" E`)*."
        ),

        "PROMPT_CAPACITY_SHIPPER": "⚖️ **Ulfaatina Fe'umsaa**\n\nUlfaatinni fe'umsaa kg tiin meeqa?",
        "PROMPT_CAPACITY_CARRIER": "⚖️ **Dandeettii Fe'umsaa**\n\nKonkolaataan keessan kg meeqa fe'uu danda'a?",
        "PROMPT_CAPACITY_PASSENGER": "👥 **Baay'ina Imaltootaa**\n\nImaltoota meeqa?",
        "PROMPT_CAPACITY_DRIVER": "💺 **Teessoo Duwwaa**\n\nTeessoo duwwaa meeqatu jira?",
        "ERR_INVALID_NUMBER_CLEAN": "⚠️ Lakkoofsa guutuu galchaa.",

        "PROMPT_CLASS_FREIGHT": "🚛 **Gosa Konkolaataa Fe'umsaa/Ramaddii Konkolaataa**\n\nGosa konkolaataa fe'umsaa "
                                "akkamii barbaaddu/qabdu? (Fkn. Baajaj Kaargoo, Isuzu FSR, FSR, Filatbeedi, "
                                "Cufamaa (Box Truck), Kan Qabbaneessu (Refrigerated))",
        "PROMPT_CLASS_RIDE": "🚘 **Gosa Konkolaataa/Sadarkaa Imalaa**\n\nGosa konkolaataa kam barbaaddu? (fkn, "
                             "Ikonomii/Diinagdee, Koomfortii/Mijataa, Minibaasii/Qananii)",
        "ERR_DESC_TOO_BRIEF": "⚠️ Ibsi baay'ee gabaabaa dha.",

        "PROMPT_EXTRAS_FREIGHT": "🚀 **Ulaagaa Addaa**\n\nGargaarsa addaa ykn ulaagaalee dandeettii fe'umsaa kamiyyuu "
                                 "komaan addaan baasii tarreessi (fkn. Salphaatti kan cabu, Meeshaa balaafamaa, "
                                 "Gargaarsa fe'iinsaa). Yoo idilee ta'e 'None' jedhii barreessi.",
        "PROMPT_EXTRAS_RIDE": "✨ **Tajaajilota Dabalataa**\n\nTajaajilota barbaadaman ykn dhiyaatan kamiyyuu "
                              "mallattoo mallassardaan (komaadhaan) addaan basuun tarreessi (fkn, AC, Kan bineensota "
                              "manaaf mijatu, Ba'aa Dabalataa)። Yoo idilee ta'e 'Hin jiru' jedhii barreessi.",
        "KEYWORD_HAZMAT": "balaafamaa",
        "KEYWORD_NONE": "hin jiru",

        "PROMPT_BUDGET_SHIPPER": "💵 **Bajata Transportii**\n\nBajata keessan ol'aanaa gosa maallaqaa ETB ykn USD/$ "
                                 "jechuun galchaa.",
        "PROMPT_BUDGET_CARRIER": "💵 **Gatii Transportii**\n\nGatii tilmaama imala kanaaf gaafattan gosa maallaqaa "
                                 "ETB ykn USD/$ ibsuun galchaa.",
        "PROMPT_BUDGET_PASSENGER": "💵 **Bajata Imalaa Ol'aanaa**\n\nBajata keessan ol'aanaa gosa maallaqaa ETB ykn "
                                   "USD/$ jechuun galchaa.",
        "PROMPT_BUDGET_DRIVER": "💵 **Gatii Imalaa**\n\nGatii imalaa gosa maallaqaa ETB ykn USD/$ jechuun galchaa.",
        "ERR_INVALID_BUDGET": "⚠️ Gatii dogoggoraa.",
        "MSG_REQUEST_PLATE": "🚗 Maaloo lakk. gabatee konkolaataa galchaa (fkn. AA-3-B12345):",
        "ERR_INVALID_PLATE": "⚠️ Akkaataa gabatee dogoggoraa.",
        "prompt_vehicle_photo": "📸 Maaloo suuraa qulqulluu Konkolaataa keessanii ol fe'aa (upload), ykn suuraa malee "
                                "itti fufuuf 'Skip' jedhaa barreessaa:",
        "prompt_asset_photo": "📸 Maaloo suuraa qulqulluu Qabeenya/Konkolaataa keessanii ol fe'aa (upload), "
                              "ykn suuraa malee itti fufuuf 'Skip' jedhaa barreessaa:",
        "PROMPT_CONTACT_INFO": "📞 **Lakk. Bilbilaa**\n\nMaaloo lakk. bilbilaa galchaa.",

        "BTN_ACCEPT_MATCH": "🔐 Mirkaneessi & Kaffali",
        "MSG_MATCH_FOUND": "🎯 **Walsimannaa Argameera!** (Qabxii: {score}%)\n\n**Gatii:** {fare}",

        "MSG_SECURE_CHECKOUT_LOAD": "⚙️ Invooyisii qopheessaa jira...",
        "BTN_PAYMENT_PORTAL": "💳 Kaffaltii Raawwadhu",
        "MSG_LOGISTICS_LOCKED": "🔐 **Waliigaltee**\n\nOdeeffannoo dhiyeessaa argachuuf koomishinii kaffalaa.",
        "ERR_CHECKOUT_FAILED": "❌ Invooyisii qopheessuun hin danda'amne.",

        # General & Status Messages
        "MSG_COMPILING_PROFILE": "⏳ Profaayilii qindeessaa fi nudoowwan logistiikaa walqabsiisaa jira...",
        "MSG_SCANNING_NODES": "🔍 Nudoota <b>{domain}</b> sakatta'aa fi maatriksii wal-simannaa qorachaa jira...",
        "MSG_REGISTRATION_COMPLETE": "✅ **Galmeen Qabeenya {domain} Milkaa'era!**",
        "MSG_REQUEST_BROADCASTED": "📡 Gaaffichi gara netwoorkii {domain} tamsaafameera. Ammatti kan wal-simu hin "
                                   "argamne, garuu yeroo argamu sin beeksifna.",
        "MSG_TOP_FREIGHT_MATCHES": "🎯 **Filannoo {count} {domain} Barbaadaman Argamaniiru!**\n\nMaaloo dhiheessii "
                                   "ergachuuf karaa armaan gadii irraa filadhu:",

        # Domains
        "domain_freight": "Fe'umsa",
        "domain_ride": "Imala",

        # Buttons & Labelstii
        "BTN_DECLINE_RETURN": "❌ Dhiisittii Deebi'i",
        "TITLE_TRANSPORT_ROUTE_FREIGHT": "Kallattii Imalaa: {origin} ➡️ {destination}",

        # Seeker Match Items
        "MSG_CARD_SEEKER_SELECTED_FREIGHT": "👤 <b>Gaaffiin Barbaadaa Filatameera:</b> {title}\n📍 <b>Kallattii:</b> "
                                            "<code>{origin} ➡️ {destination}</code>\n{details}💰 <b>Baajata "
                                            "Olaanaa:</b> <code>{budget} {currency}</code>\n\n<i>Dhiheessii keessan "
                                            "barbaada kanaaf ergachuuf filannoo armaan gadii filadhu:</i>",
        "BTN_ACCEPT_DEAL": "✅ Fudhadhu",
        "BTN_COUNTER_OFFER": "📉 Gatii Kaadhimuu",
        "BTN_DECLINE_DEAL": "❌ Dhiisi",
        "NEGOTIATION_TITLE": "🔄 Dhiheessii Haaraa",
        "TRANSACTION_LABEL": "Gosa Daldalaa",
        "ASSET_LABEL": "Qabeenya/Fe'umsa",
        "OFFERED_PRICE": "Gatii Dhihaate",
        "EVALUATE_PROMPT": "Maaloo gatii dhihaate madaaluun filannoo armaan gadii irraa murtoo kennaa.",
        "ERR_SYS_PIPELINE_FAILED": "⚠️ Rakkoon sirnaa mudatee jira. Maaloo irra deebi'attii yaalaa.",
        "err_vehicle_not_found": "⚠️ Konkolaataan filatame ammata hin argamu.",
        "MSG_SELECTED_MATCH_SUMMARY": "{domain_emoji} <b>Maqaa Walsimannaa Filatame:</b>\n<i>{asset_title}</i>\n\n💰 "
                                      "<b>Gatii:</b> <code>{fare} ETB</code>",
        "msg_enter_custom_offer": "💬 <b>Gatii Keessan Galchaa</b>",
        "MSG_PROMPT_CUSTOM_OFFER": "{header_text}\n\n*Ibsa:*\n_{asset_title}_\n\nGatii: `{asking_price} "
                                   "ETB`\n\nMaaloo gatii keessan galchaa:",
        "err_invalid_price": "⚠️ <b>Gatii Dogoggoraa</b>",
        "MSG_OFFER_TRANSMITTING": "⏳ <b>Gatii Ergaa Jira...</b>",
        "ERR_VERIFY_PROVIDER": "⚠️ Dogoggora sirnaa.",
        "MSG_TRANSMITTING_OFFER": "🔄 Gaaffii ergaa jira...",
        "MSG_OFFER_TRANSMITTED_SUCCESS": "✅ <b>Gatiin Ergameera!</b>",
        "ERR_PENDING_NEGOTIATION": "🚫 **Waliigalteen Dhorkameera**",
        "ERR_DEAL_INIT_FAILED": "⚠️ Dogoggora sirnaa.",
        "MSG_PROPOSE_OFFER_HEADER": "Yaada Gatii Dhiyeessi",
        "LBL_ASSET": "Qabeenya / Meeshaa",
        "LBL_ASKING_RATE": "Gatii Gaafatamu",
        "MSG_PROPOSE_OFFER_INSTRUCTION": "Ibsa olii ilaalaatii gatii fudhadhaa ykn dhiyeessii keessan dhiyeessaa.",

        # Verticals
        "vertical_housing": "Mana",
        "vertical_vehicle": "Konkolaataa",
        "vertical_equipment": "Meeshaa",
        "vertical_freight": "Fe'umsa",
        "vertical_ride": "Imala",

        # Transactions
        "tx_rental": "Kiraa",
        "tx_sale": "Gurgurtaa",
        "tx_lease": "Liizii",

        # Logistics Labels
        "lbl_route": "Karaa",
        "lbl_plate": "Lakk. Gabatee",
        "lbl_weight": "Ulfaatina",
        "lbl_party": "Baay'ina Imaltootaa",
        "lbl_category": "Ramaddii",
        "lbl_transaction": "Daldala",

        "LABEL_ROUTE": "Karaa",
        "LABEL_PLATE": "Gabatee",
        "LABEL_WEIGHT": "Ulfaatina",
        "LABEL_PARTY_SIZE": "Imaltoota",

        # --- Requests & Processing ---
        "SYS_PROCESSING": "⏳ <b>Gaaffiin keessan hojjetamaa jira...</b>\n\n<i>Maaloo adeemsa mariichaa fi qopheessuu "
                          "liinkii kaffaltii hamma xumurruutti obsaan nu eegaa.</i> 🔄",
        "ERR_MISSING_SEEKER_LISTING": "⚠️ Dogoggora Sirnaaa: Seeker mirkanaa'ee ykn ID dhabameera.",
        "ERR_NEGOTIATION_UNAVAILABLE": "⚠️ Dogoggora Sirnaaa: Tajaajilli mariichaa ammatti hin jiru.",
        "ERR_SYNC_STREAM": "⚠️ Dogoggorri Sirnaaa: Adeemsa marii wal simate jalqabuu hin danda'amne.",

        # --- Success Messages ---
        "SUCCESS_PROVIDER_OFFER": (
            "✅ <b>Dhiheessiin Ergameera!</b>\n\n"
            "🏠 <b>Qabeenya/Gaaffii:</b> <i>{escaped_title}</i>\n"
            "💰 <b>Gatii Dhihaate:</b> <b>{formatted_price} {currency}</b>\n\n"
            "Dhiheessiin kee haala milkaa'ina qabuun barbaadaaf ergameera."
        ),
        "SUCCESS_DEAL_BID": (
            "✅ <b>Dhiheessiin Ergameera!</b>\n\n"
            "🏠 <b>Qabeenya/Tajaajila:</b> <i>{escaped_title}</i>\n"
            "💰 <b>Gatii Dhihaate:</b> <b>{formatted_price} {currency}</b>\n\n"
            "Dhiheessiin kee haala milkaa'ina qabuun dhiyeessaaf dabarfameera."
        ),

        # --- Pipeline Blocks ---
        "BLOCK_NEGOTIATION": (
            "🚫 <b>Adeemsi Falmii Dangeeffameera</b>\n\n"
            "Qabeenyi kun ammaan tana marii/falmii keessa jira ykn beeksisa keessan irratti gatii dhiyeessuu hin "
            "dandeessan."
        ),

        # --- Cards & Detail Labels ---
        "LABEL_CARGO_WEIGHT": "Ulfaatina Fe'umsaa",

        # --- Common Values / Fallbacks ---
        "VAL_NEGOTIABLE": "Marii'atamaa",
        "VAL_ORIGIN_DEFAULT": "Bakka Ka'umsaa",
        "VAL_DEST_DEFAULT": "Bakka Gahuumsaa",

        # Notifications & Templates
        "deal_agreed_provider_confirmation": (
            "✅ Gatii <b>{price} {currency}</b> <i>{asset_title}</i> tiif dhiyaate fudhattanii jirtu. "
            "Kaffaltii tajaajilaa bitataa irra eegaa jira.\n{details}"
        ),
        "deal_agreed_seeker_invoice": (
            "🎉 <b>Gatiin Fudhatama Argateera!</b>\n\n"
            "Dhiyeessaan gatii keessan <b>{price} {currency}</b> <i>{asset_title}</i>'f fudhateera.\n"
            "{details}\nOdeeffannoo guutuu fi lakk. bilbilaa argachuuf koomishinii kaffalaa."
        ),
        "deal_agreed_seeker_confirmation": (
            "✅ <b>Gatiin Haaraa Fudhatama Argateera!</b>\n\n"
            "Gatii <b>{price} {currency}</b> fudhattaniitu.\n{details}"
            "\nOdeeffannoo guutuu fi lakk. bilbilaa argachuuf koomishinii kaffalaa."
        ),
        "deal_agreed_provider_notice": (
            "🎉 <b>Gatiin Haaraa Fudhatama Argateera!</b>\n\n"
            "Barbaadaan gatii <b>{price} {currency}</b> <i>{asset_title}</i>'f fudhateera."
            "\n Kafaltii eegaa jira."
        ),
        "btn_pay_local": "🇪🇹 Kaffaltii Biyya Keessaa ({price} ETB)",
        "btn_pay_intl": "🌍 Kaardii Idil-addunyaa / PayPal",

        # ----------------------------------------------------
        # WEBHOOKS
        # ----------------------------------------------------
        "DEFAULT_DRIVER_NAME": "Ooftuu Sirnaa",
        "MSG_WEBHOOK_SUCCESS": "✅ **Kaffaltiin Mirkanaa'eera!**\n\nLakk. Waliigaltee: `{deal_id}`\n\n**Ooftuu:** {"
                               "driver_name}\n**Bilbila:** {contact}\n**Gabatee:** {vehicle_plate}",

        # ================================================
        # --- MATCHING & DEALS (deal_) ---
        # ================================================
        "deal_match_found": "🎉 *Walsimannaan Argameera!*",
        "deal_proposed": "🤝 *Waliigaltee Haaraa!*",
        "deal_btn_accept": "✅ Waliigaltee Fudhadhu",
        "deal_btn_decline": "❌ Waliigaltee Didu",
        "deal_accepted_seeker": "✅ Waliigaltee fudhattaniirtu!",
        "deal_accepted_provider": "🎉 *Waliigalteen Mirkanaa'eera!*",
        "deal_declined": "🚫 Waliigalteen dhiroomeera.",
        "deal_contact_info": "📞 *Odeeffannoo Qunnamsiisaa:*\nMaqaa: {name}\nBilbila: {phone}",

        "TITLE_TRANSPORT_ROUTE": "Karaa Geessimmaa: {origin} ➡️ {destination}",
        "MSG_CARD_MATCH_SUMMARY_FREIGHT": (
            "🚚 <b>Gudunfaa Filannoo:</b>\n\n"
            "{title}\n"
            "📍 <b>Karaa:</b> {origin} ➡️ {destination}\n"
            "💰 <b>Gatii Tilmaamaa:</b> <b>{fare} {currency}</b>\n\n"
            "Dhiheessii keessan erguuf armaan gadii filadhaa:"
        ),
        "BTN_SEND_ASKING_RATE": "✅ Gatii Gaafatame Ergii ({price} {currency})",
        "BTN_ACCEPT_FARE": "✅ Gatii Fudhadhu ({fare} {currency})",
        "BTN_BACK_TOP_MATCHES": "❌ Deebi'i",
        "ERR_REQUEST_NOT_FOUND": "⚠️ Gaaffiin filatame dhumateera ykn haqameera.",
        "ERR_VEHICLE_NOT_FOUND": "⚠️ Konkolaataan filatame amma hin jiru.",

        # REVERSE_MATCH_STRINGS
        "scanning": "🔍 Gaaffiiwwan barbaaddotaa hojii irra jiran sakatta'aa jira...",
        "no_matches": "✅ **Tarsasa'iinii Hojii Irra Ooleera!**\nAmmaaf, barbaadduun gosa kana kallattiin gaafate hin "
                      "jiru. Ofumaan isiniif beeksifama.",
        "top_seekers": "🎯 **Barbaaddotni ulaagaa keessaniin walgitamu {count} argamaniiru!**\nDhiheessii keessan "
                       "erguuf armaan gadii filadhaa:",
        "btn_match": "👤 #{index} Barbaaddaa (Baajeta: {budget} {currency} - {score}%)",
        "btn_skip": "Cehi",
        "error_skipped": "✅ Tarsasa'iini milkaawinaan uumameera. (Rakkina sirnaatiin sakatta'iinsi cehameera).",
        "MSG_TOP_SEEKER_MATCHES": "🎯 **Barbaaddotni ulaagaa keessaniin walgitamu {count} {vertical} "
                                  "argamaniiru!**\n\nDhiheessii keessan erguuf armaan gadii filadhaa:",
        "BTN_SEEKER_MATCH_ITEM": "👤 #{index} Barbaaddaa (Baajeta: {budget} {currency} - {score}%)",

        # ==========================================
        # DEALS HANDLER
        # ==========================================
        "prompt_enter_counter_price": "Maaloo gatii haaraa galchaa:",
        "btn_cancel_counter": "❌ Waliigaltee Haqi",
        "err_action_required_counter": "⚠️ **Tarkaanfiin Barbaachisaa Dha**",
        "err_sys_general": "⚠️ Dogoggora sirnaa mudateera. Maaloo irra deebii yaali.",
        "error_system_state": "⚠️ Dogoggora sirnaa.",
        "error_invalid_numeric_input": "⚠️ Lakkoofsa dogoggoraa.",
        "error_deal_expired": "⚠️ Waliigalteen kun yeroon itti darbeera.",
        "notice_provider_countered": "🔔 Dhiyeessaan gatii haaraa dhiyeesseera: *{price} ETB*",
        "notice_seeker_countered": "🔔 Bitaan gatii haaraa dhiyeesseera: *{price} ETB*",
        "btn_counter_offer": "💬 Gatii Haaraa Dhiyeessii",
        "btn_decline_deal": "🚫 Waliigaltee Didu",
        "success_counter_transmitted": "✅ Gatiin haaraa *{price} ETB* ergameera.",
        "error_unexpected": "⚠️ Dogoggorri hin eegamne uumameera.",
        "notice_counter_aborted": "❌ Gatiin haaraa haqameera.",
        "notice_action_cancelled": "❌ Gochi haqameera.",
        "error_transaction_invalid": "⚠️ Gaaffii daldalaa dogoggoraa.",
        "error_execution": "⚠️ Gocha raawwachuun hin danda'amne.",
        "btn_pay_commission": "💳 Koomishinii Kaffali",
        "notice_deal_declined_self": "🚫 Waliigaltee dhiistaniirtu.",
        "notice_deal_declined_opposing": "🚫 Gamoon biraa waliigaltee dhiiseera.",
        "msg_missed_alerts": "🔔 **Beeksisa Cufaman:**",
        "lbl_queued_notification": "🔔 Beeksisa",
        "msg_queue_cleared_summary": "✅ **Beeksisoonni Hundumtuu Ergamaniiru.**",
        "err_queue_delivery_failed": "⚠️ *Beeksisa erguun hin danda'amne.*",
        "msg_queue_overflow": "⚠️ **Hubachiisa:** Beeksisoonni moofaan harca'aniiru.",

        "error_billing_pipeline": "⚠️ Tajaajilli kaffaltii hin hojjenne.",
        "msg_negotiation_cancelled": "❌ Gochi haqameera.",
        "err_unauthorized_action": "⚠️ Gocha hin eeyyamamne.",
        "err_sys_invalid_price": "⚠️ Dogoggora sirnaa.",

        # --- Notification Templates ---
        "notice_new_offer_template": (
            "🔔 *Dhiheessii {tx_type} Haaraa ({vertical_type})!*\n\n"
            "🏷️ *Gosa Daldalaa:* {tx_type}\n"
            "🏠 *Qabeenya:* _{asset_title}_\n"
            "{attributes}"
            "💰 *Gatii Dhihaate:* *{price} {currency}*"
        ),

        "notice_offer_sent_seeker": "✅ Dhiheessiin kee dhiyeessaaf ergameera: *{price} {currency}*",
        "notice_reverse_offer_sent_provider": "✅ Dhiheessiin kee barbaadaaf ergameera: *{price} {currency}*",

        # --- Localized Terms ---
        "verticals": {
            "housing": "Mana",
            "vehicle": "Konkolaataa",
            "equipment": "Meeshaalee",
            "freight": "Fe'umsaa",
            "ride": "Imala",
            "general": "Qabeenya"
        },
        "transactions": {
            "rental": "Kiraa",
            "rent": "Kiraa",
            "sale": "Gurgurtaa",
            "lease": "Liizii",
            "service": "Tajaajila",
            "booking": "Qabiinsa"
        },

        "msg_counter_prompt": "Maaloo gatii haaraa galchaa:",
        "msg_counter_sent": "✅ Gatiin haaraa {price} {currency} ergameera!",
        "notice_reverse_offer_sent_seeker": "✅ Dhiheessiin kee barbaadaaf ergameera: *{price} {currency}*",
        "notice_reverse_offer_template": (
            "🔄 *Dhiyeessii Wal-gitiinsaa Garagaltoo Haaraya ({vertical_type})!*\n"
            "Dhiyeessaan gaaffii tajaajila keessan irratti gatii dhiyeessee jira:\n"
            "🏷️ *Iddoo Guriisaa (Transaction):* {tx_type}\n"
            "🏠 *Qabeenya:* _{asset_title}_\n"
            "{attributes}\n"
            "💰 *Gatii Dhiyaate:* *{price} {currency}*"
        ),

        "deal_agreed_provider_counter_accepted": "✅ Gatii jijjiiramaa _{asset_title}_f *{price} {currency}* "
                                                 "fudhatteetta. Kafaltii komishinii barbaaddichaa eeggachaa jira.\n{"
                                                 "details}",
        "deal_agreed_seeker_counter_accepted": "🎉 *Gatiin Wal Jijjiirame Fudhatameera!*\n\nDhiyeessaan gatii "
                                               "jijjiiramaa _{asset_title}_f *{price} {currency}* fudhateera.\n{"
                                               "details}\nOodeeffannoo dabalataa fi bilbila quunnamtii argachuuf, "
                                               "maaloo kaffaltii komishinii gaditti ibsame raawwadhaa.",
        "deal_agreed_seeker_offer_accepted": "✅ *Dhiheessiin Fudhatameera!*\n\nDhiheessii _{asset_title}_f *{price} {"
                                             "currency}* fudhatteetta.\n{details}\nOodeeffannoo dabalataa fi bilbila "
                                             "quunnamtii argachuuf, maaloo kaffaltii komishinii gaditti ibsame "
                                             "raawwadhaa.",
        "deal_agreed_provider_offer_accepted": "🎉 *Dhiheessiin Fudhatameera!*\n\nBarbaadaan dhiheessii kee _{"
                                               "asset_title}_f *{price} {currency}* fudhateera.\n{details}\nKafaltii "
                                               "komishinii barbaaddichaa eegaa jirra.",

        # ====================================================================
        # --- VERIFICATION (verify_) ---
        # ====================================================================
        "verify_prompt": "🛡️ Nageenya bakka gabaa keenyaaf, maaloo suuraa Waraqaa Eenyummaa ykn Eeyyama Oofummaa ol "
                         "fe'a.",
        "verify_pending": "⏳ Waraqaan eenyummaa AI'n mirkanaa'aa jira...",
        "verify_success": "✅ Mirkaneessuun milkaa'eera!",
        "verify_failed": "❌ Mirkaneessuun hin milkaawne.",
        "verify_start_message": "🛡️ **Mirkaneessuu Smart Match Africa**\n\nMaaloo suuraa qulqulluu **Eeyyama "
                                "Oofummaa ykn Waraqaa Eenyummaa** ol fe'a.",
        "error_invalid_input_photo_required": "⚠️ **Gosa Galtee Dogoggoraa**",
        "verify_processing_document": "🔒 **Waraqaan Dhiyaateera!**",
        "verify_upload_failed": "❌ Sirni koobaa jira.",
        "verify_success_message": "✅ **Mirkaneessuun Milkaa'eera!**",
        "verify_failed_validation": "❌ **Mirkaneessuun Kufileera**",
        "error_system_timeout": "⚠️ **Yeroon Sirnaa Dhumateera**",
        "verify_cancelled_message": "🛑 **Mirkaneessuun Dhaabbateera**",

        # -- webhook_handler.py translations for dispatch section
        "dispatch_payment_confirmed": "✅ <b>Kaffaltiin Mirkanaa'eera!</b>",
        "dispatch_seeker_body": "Daldalli keessan <b>{asset_title}</b> (Lakk. Waliigaltee: <code>{deal_id}</code>) "
                                "xumurameera.",
        "dispatch_match_finalized": "✅ <b>Walsimannaan Xumurameera!</b>",
        "dispatch_provider_body": "Kaffaltiin koomishinii kaffalameera: <b>{asset_title}</b> (Lakk. Waliigaltee: "
                                  "<code>{deal_id}</code>).",
        "dispatch_category": "📂 <b>Ramaddii:</b> <code>{vertical_name}</code>",
        "dispatch_transaction": "🏷️ <b>Daldala:</b> <code>{transaction_type}</code>\n",
        "dispatch_asset_details": "🚙 <b>Ibsa Qabeenyaa:</b> <code>{mmy}</code>",
        "dispatch_location": "📍 <b>Teessoo:</b> <code>{location}</code>",
        "dispatch_provider_phone": "📞 <b>Bilbila Dhiyeessaa:</b> <code>{phone}</code>",
        "dispatch_client_phone": "📞 <b>Bilbila Maamilaa:</b> <code>{phone}</code>",
        "not_specified": "Hin ibsamne",
        "person_unit": "Nama",
        "people_unit": "Nama",

        "dispatch_route": "📍 <b>Karaa:</b> <code>{origin} ➡️ {destination}</code>\n",
        "dispatch_cargo_weight": "⚖️ <b>Ulfaatina Fe'umsaa:</b> <code>{weight} kg</code>\n",
        "dispatch_plate_number": "🚙 <b>Lakk. Gabatee:</b> <code>{plate}</code>\n",
        "dispatch_party_size": "👥 <b>Baay'ina Imaltootaa:</b> <code>{party}</code>\n",
        "lbl_not_specified": "Hin ibsamne",
        "dispatch_seeker_footer": "Dhiyeessaa wajjin kallattiin qunnamaa. Smart Match Africa fayyadamuu keessaniif "
                                  "galatoomaa!",
        "dispatch_provider_footer": "Bilbila isaanii dhiyootti eegaa, ykn jalqabuuf kallattiidhaan isaan quunnamaa. "
                                    "Smart Match Africa fayyadamuu keessaniif galatoomaa!",

        # Missing Keys Added
        "LABEL_LOGISTICS_PROFILE": "Profaayilii Loojistikii",
        "LABEL_ESTIMATED_FARE": "Tilmaama Kaffaltii",
        "PROMPT_ENTER_OFFER_AMOUNT": "Maaloo gatii ofii keessanii galchaa:"
    }
}


# ==========================================
# 3. TEXT RETRIEVAL & ENGINE FUNCTIONS
# ==========================================
def get_text(lang_code: str, key: str, **kwargs) -> str:
    """
    Core function to retrieve translated text safely.
    Automatically applies string formatting kwargs and converts HTML tags to Telegram Markdown.

    Args:
        lang_code (str): The user's language code ('en', 'am', 'om'). Defaults to 'en'.
        key (str): The dictionary key for the required text.
        **kwargs: Optional string formatting arguments (e.g., price="5000")

    Returns:
        str: The translated, sanitized string, gracefully falling back to English or returning a debug string.
    """
    if not key:
        return ""

    # Normalize language code to supported languages, defaulting to English
    if not lang_code or lang_code not in TRANSLATIONS:
        lang_code = "en"

    # 1. Attempt to fetch the translation in the requested language
    text_template = TRANSLATIONS[lang_code].get(key)

    # 2. Fallback mechanism: If key is missing in 'am' or 'om', fetch from 'en'
    if text_template is None:
        if lang_code != "en":
            logger.warning(f"Translation key '{key}' missing for language '{lang_code}'. Falling back to 'en'.")

        # Try fetching from English
        text_template = TRANSLATIONS.get("en", {}).get(key)

    # 3. Ultimate fallback: If the key doesn't exist in English either (helps debugging)
    if text_template is None:
        logger.error(f"CRITICAL: Translation key '{key}' completely missing in all languages!")
        text_template = f"[{key}]"

    # 4. Inject variables safely and sanitize
    if kwargs:
        try:
            formatted_text = text_template.format(**kwargs)
            return sanitize_telegram_markdown(formatted_text)
        except KeyError as e:
            logger.error(f"Formatting KeyError for key '{key}' (lang: {lang_code}): missing kwarg {e}")
            return sanitize_telegram_markdown(text_template)  # Return unformatted string to prevent crash
        except ValueError as e:
            logger.error(f"Formatting ValueError for key '{key}' (lang: {lang_code}): {e}")
            return sanitize_telegram_markdown(text_template)
        except Exception as e:
            logger.error(f"Unexpected formatting error for key '{key}' (lang: {lang_code}): {str(e)}")
            return sanitize_telegram_markdown(text_template)

    # 5. Sanitize string if no formatting arguments were passed
    return sanitize_telegram_markdown(text_template)


def get_keyboard_labels(key: str) -> List[str]:
    """
    Reverse-lookup helper. Returns a list of all unique language variants for a specific button.
    This is extremely useful for MessageHandler regex filters so the bot recognizes
    a button click regardless of the language the user is currently using.

    Example:
        get_keyboard_labels("btn_cancel")
        -> ["❌ Cancel", "❌ ሰርዝ", "❌ Haqii (Cancel)"]
    """
    if not key:
        return []

    labels = []

    # Iterate dynamically through all supported languages
    for lang_code, lang_dict in TRANSLATIONS.items():
        val = lang_dict.get(key)
        if val:
            labels.append(val)

    # Fallback if the button key isn't found anywhere
    if not labels:
        logger.warning(f"Keyboard label key '{key}' not found in any language dictionary.")
        return [key]

    # Remove duplicates while preserving order (e.g., if 'am' falls back to 'en' text)
    # This optimizes regex matching by not checking identical strings twice
    unique_labels = list(dict.fromkeys(labels))

    return unique_labels


def get_vertical_name(lang_code: Optional[str], vertical_key: Optional[str]) -> str:
    """
    Resolves domain vertical strings (e.g. 'housing', 'machinery', 'farming')
    into localized category display names using the master translation dictionary.
    """
    if not vertical_key:
        return get_text(lang_code, "vertical_general")

    clean_key = str(vertical_key).strip().lower().replace(" ", "_")

    # Map common aliases or structural keys
    alias_map = {
        "residential": "vertical_housing",
        "commercial": "vertical_housing",
        "housing": "vertical_housing",
        "vehicle": "vertical_vehicle",
        "transport": "vertical_vehicle",
        "freight": "vertical_freight",
        "ride": "vertical_ride",
        "equipment": "vertical_equipment",
        "machinery": "vertical_machinery",
        "construction": "vertical_machinery",
        "farming": "vertical_farming",
        "agriculture": "vertical_farming",
        "industrial": "vertical_industrial"
    }

    translation_key = alias_map.get(clean_key, f"vertical_{clean_key}")
    localized = get_text(lang_code, translation_key)

    # If key wasn't found in translation dictionary (i.e. returns a debug string starting with '[')
    if localized.startswith("[") and localized.endswith("]"):
        return str(vertical_key).replace("_", " ").title()

    return localized
