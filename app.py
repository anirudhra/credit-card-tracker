#!/usr/bin/env python3
"""
Credit Card Tracker — Self-Hosted Multi-Card Benefits, Rotating Categories & Live Vendor Advisor
Inspired by r/ChaseSapphire (post 1xdwji9: "How I track my CSR benefits").

Supports the complete 7-card portfolio:
1. Chase Sapphire Reserve (Refreshed $795 AF & Legacy $550 AF)
2. Chase Freedom ($0 AF — 5% / 5x Rotating Quarterly Categories, transferable to CSR!)
3. Discover it Cash Back ($0 AF — 5% Rotating Quarterly Categories)
4. Amex EveryDay Credit Card ($0 AF — 20% Monthly 20-Swipe Bonus & 2x Groceries)
5. Amex Blue Cash Everyday ($0 AF — $84 Disney, $180 Home Chef, 3% Online/Grocery/Gas Caps)
6. Bank of America Customized Cash Rewards ($0 AF — 3% Choice Category + 2% Grocery/Wholesale Clubs up to $2,500/qtr, Museums on Us)
7. Bank of America Travel Rewards ($0 AF — 1.5x–2.62x Catch-All, No FTF, Museums on Us)
"""

from __future__ import annotations

import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
import re
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import vendor_engine

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CC_TRACKER_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "credit_card_tracker.db"
STATIC_DIR = BASE_DIR / "static"

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FULL_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

AUTO_SYNC_INTERVAL_SECONDS = 24 * 3600  # 24 hours (daily auto-sync)
_SYNC_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Official Credit Card Website Sources for Live Querying
# ---------------------------------------------------------------------------
ISSUER_SOURCES: list[dict[str, str]] = [
    {
        "preset_key": "discover_it",
        "card_name": "Discover it® 5% Cashback Calendar",
        "issuer": "Discover",
        "url": "https://www.discover.com/credit-cards/cash-back/cashback-calendar.html",
        "activation_url": "https://www.discover.com/credit-cards/cash-back/cashback-calendar.html",
        "verified_rules": "5% quarterly rotating categories (up to $1,500/qtr after activation): Q1 Groceries/Wholesale Clubs/Streaming • Q2 Restaurants & Home Improvement • Q3 Gas/EV/Transit/Drugstores • Q4 Restaurants/Entertainment/Utilities.",
    },
    {
        "preset_key": "csr_795",
        "card_name": "Chase Sapphire Reserve®",
        "issuer": "Chase",
        "url": "https://creditcards.chase.com/rewards-credit-cards/sapphire/reserve",
        "activation_url": "https://ultimaterewards.chase.com/",
        "verified_rules": "8x Chase Travel • 4x Direct Flights & Hotels • 3x Global Dining • $300 Travel Credit • $500 The Edit • $300 Exclusive Tables • $300 StubHub • $35/mo DoorDash • $10/mo Lyft • $10/mo Peloton.",
    },
    {
        "preset_key": "chase_freedom",
        "card_name": "Chase Freedom® 5% Bonus Calendar",
        "issuer": "Chase",
        "url": "https://www.chasebonus.com/",
        "activation_url": "https://www.chasebonus.com/",
        "verified_rules": "5% / 5x UR quarterly rotating categories (up to $1,500/qtr = $75 or 7,500 UR pts transferable to CSR): Q1 Groceries/Fitness/Cruises • Q2 Amazon/Whole Foods/Streaming • Q3 Gas/EV/Live Entertainment/Movies • Q4 PayPal/McDonald's/Pet Shops/Charities.",
    },
    {
        "preset_key": "amex_bce",
        "card_name": "Amex Blue Cash Everyday®",
        "issuer": "American Express",
        "url": "https://www.americanexpress.com/us/credit-cards/card/blue-cash-everyday/",
        "activation_url": "https://global.americanexpress.com/card-benefits",
        "verified_rules": "3% U.S. Online Retail (Amazon, eBay, Gap.com, Target.com, Walmart.com — up to $6k/yr) • 3% U.S. Supermarkets ($6k/yr) • 3% U.S. Gas Stations ($6k/yr) • $7/mo Disney Bundle • $15/mo Home Chef.",
    },
    {
        "preset_key": "amex_everyday",
        "card_name": "Amex EveryDay® Credit Card",
        "issuer": "American Express",
        "url": "https://www.americanexpress.com/us/credit-cards/card/amex-everyday/",
        "activation_url": "https://global.americanexpress.com/offers",
        "verified_rules": "2x U.S. Supermarkets ($6k/yr) • 1x Catch-All • 20+ purchases in a billing period = +20% bonus points (2.4x Supermarkets / 1.2x Catch-All) • Amex Offers.",
    },
    {
        "preset_key": "boa_ccr",
        "card_name": "Bank of America® Customized Cash Rewards",
        "issuer": "Bank of America",
        "url": "https://www.bankofamerica.com/credit-cards/products/cash-back-credit-card/",
        "activation_url": "https://www.bankofamerica.com/credit-cards/manage-your-credit-card-account/",
        "verified_rules": "3% Choice Category (Online Shopping [incl. Cable/Internet/Phone/Streaming], Gas/EV, Dining, Travel, Drug Stores, or Home Improvement) + 2% Grocery & Wholesale Clubs (Costco Visa!) up to $2,500/qtr • Museums on Us • BankAmeriDeals.",
    },
    {
        "preset_key": "boa_travel",
        "card_name": "Bank of America® Travel Rewards",
        "issuer": "Bank of America",
        "url": "https://www.bankofamerica.com/credit-cards/products/travel-rewards-credit-card/",
        "activation_url": "https://www.bankofamerica.com/credit-cards/manage-your-credit-card-account/",
        "verified_rules": "1.5x Unlimited Points (up to 2.62x w/ Preferred Rewards) • 3x BoA Travel Center • $0 FTF • Museums on Us (1st full weekend/mo) • BankAmeriDeals.",
    },
]


# ---------------------------------------------------------------------------
# Default Card Presets (Full 7-Card Wallet)
# ---------------------------------------------------------------------------
PRESET_CARDS: dict[str, dict[str, Any]] = {
    "csr_795": {
        "name": "Chase Sapphire Reserve (Refreshed $795)",
        "preset_key": "csr_795",
        "issuer": "Chase",
        "annual_fee": 795.0,
        "au_fee": 195.0,
        "au_count": 0,
        "anniversary_date": "2026-11-01",
        "color_theme": "sapphire",
        "earning_summary": "8x Chase Travel • 4x Direct Flights & Hotels • 3x Global Dining • 1x All Else (No FTF)",
        "perks": [
            {
                "name": "$300 Annual Travel Credit",
                "category": "Travel",
                "credit_type": "hard_credit",
                "frequency": "anniversary",
                "period_amount": 300.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Auto-applied to airlines, hotels, car rentals, transit, parking, tolls. Resets on cardmember anniversary. Does not earn points.",
                "sort_order": 1,
            },
            {
                "name": "The Edit by Chase Travel Hotel Credit",
                "category": "Travel",
                "credit_type": "hard_credit",
                "frequency": "semi_annual_bookings",
                "period_amount": 250.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Up to $250 per prepaid booking (2-night minimum stay) through The Edit by Chase Travel, up to 2 bookings ($500/yr) per calendar year.",
                "sort_order": 2,
            },
            {
                "name": "Exclusive Tables Dining Credit",
                "category": "Dining",
                "credit_type": "hard_credit",
                "frequency": "semi_annual",
                "period_amount": 150.0,
                "requires_activation": 1,
                "activation_done": 0,
                "caveats": "$150 Jan–Jun and $150 Jul–Dec at participating restaurants via OpenTable Sapphire Reserve Exclusive Tables. Activation required.",
                "sort_order": 3,
            },
            {
                "name": "StubHub & viagogo Event Credit",
                "category": "Entertainment",
                "credit_type": "hard_credit",
                "frequency": "semi_annual",
                "period_amount": 150.0,
                "requires_activation": 1,
                "activation_done": 0,
                "caveats": "$150 Jan–Jun and $150 Jul–Dec for concerts, sports, and live event tickets on StubHub or viagogo. Activation required.",
                "sort_order": 4,
            },
            {
                "name": "DoorDash All-Eligible Monthly Credit ($15/mo)",
                "category": "Dining",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 15.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "$15/month promo credit usable on all eligible DoorDash orders including restaurants (upgraded from $5/mo). Requires active DashPass.",
                "sort_order": 5,
            },
            {
                "name": "DoorDash Grocery / Retail Promo #1 ($10/mo)",
                "category": "Lifestyle",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "First $10/month non-restaurant DoorDash promo (grocery, convenience, DashMart, retail). Pickup at DashMart/7-Eleven avoids delivery fees.",
                "sort_order": 6,
            },
            {
                "name": "DoorDash Grocery / Retail Promo #2 ($10/mo)",
                "category": "Lifestyle",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Second $10/month non-restaurant DoorDash promo (must be applied on a separate order from Promo #1).",
                "sort_order": 7,
            },
            {
                "name": "Lyft Monthly Ride Credit ($10/mo)",
                "category": "Travel",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "$10/month in-app credit for eligible rides in the US & Canada when CSR is set as payment method in the Lyft app.",
                "sort_order": 8,
            },
            {
                "name": "Peloton Membership Credit ($10/mo)",
                "category": "Fitness",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 0,
                "caveats": "$10/month statement credit toward eligible Peloton memberships (App+, All-Access, Guide). Requires activation.",
                "sort_order": 9,
            },
            {
                "name": "Apple TV+ & Apple Music Subscriptions",
                "category": "Subscriptions",
                "credit_type": "lifestyle_perk",
                "frequency": "monthly",
                "period_amount": 27.0,
                "requires_activation": 1,
                "activation_done": 0,
                "caveats": "Complimentary Apple TV+ and Apple Music individual subscriptions ($324/yr combined value). Activate each through Chase Benefits.",
                "sort_order": 10,
            },
            {
                "name": "Complimentary DashPass Membership",
                "category": "Subscriptions",
                "credit_type": "lifestyle_perk",
                "frequency": "annual",
                "period_amount": 120.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "$0 delivery fees and reduced service fees on eligible DoorDash orders ($120/yr retail value).",
                "sort_order": 11,
            },
            {
                "name": "Global Entry / TSA PreCheck / NEXUS Credit",
                "category": "Travel",
                "credit_type": "hard_credit",
                "frequency": "quadrennial",
                "period_amount": 120.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Up to $120 statement credit once every 4 years for application fee charged to the card.",
                "sort_order": 12,
            },
        ],
    },
    "chase_freedom": {
        "name": "Chase Freedom® (5% Rotating)",
        "preset_key": "chase_freedom",
        "issuer": "Chase",
        "annual_fee": 0.0,
        "au_fee": 0.0,
        "au_count": 0,
        "anniversary_date": "2026-06-01",
        "color_theme": "sapphire",
        "earning_summary": "5% / 5x Rotating Quarterly Categories (up to $1,500 spend = $75 or 7,500 UR pts/qtr — transferable to CSR!) • 5x Chase Travel • 1x All Else",
        "perks": [
            {
                "name": "5% Q1 Rotating: Groceries, Fitness Clubs & Norwegian Cruise Line ($75 / 7,500 UR Max)",
                "category": "Rotating 5%",
                "credit_type": "hard_credit",
                "frequency": "q1_only",
                "period_amount": 75.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Jan–Mar: Earn 5% cash back (7,500 Ultimate Rewards points) on up to $1,500 in combined purchases at Grocery Stores (excl. Walmart/Target), Fitness Clubs & Gym Memberships, and Norwegian Cruise Line. Must activate at ChaseBonus.com!",
                "sort_order": 1,
            },
            {
                "name": "5% Q2 Rotating: Amazon.com, Whole Foods & Select Streaming ($75 / 7,500 UR Max)",
                "category": "Rotating 5%",
                "credit_type": "hard_credit",
                "frequency": "q2_only",
                "period_amount": 75.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Apr–Jun: Earn 5% cash back (7,500 Ultimate Rewards points) on up to $1,500 in combined purchases at Amazon.com, Whole Foods Market, and Select Streaming Services. Must activate at ChaseBonus.com!",
                "sort_order": 2,
            },
            {
                "name": "5% Q3 Rotating: Gas Stations, EV Charging, Live Entertainment & Movie Theaters ($75 / 7,500 UR Max)",
                "category": "Rotating 5%",
                "credit_type": "hard_credit",
                "frequency": "q3_only",
                "period_amount": 75.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Jul–Sep: Earn 5% cash back (7,500 Ultimate Rewards points) on up to $1,500 in combined purchases at Gas Stations, EV Charging, Select Live Entertainment, and Movie Theaters. Must activate at ChaseBonus.com!",
                "sort_order": 3,
            },
            {
                "name": "5% Q4 Rotating: PayPal, McDonald's, Pet Shops/Vet & Charities ($75 / 7,500 UR Max)",
                "category": "Rotating 5%",
                "credit_type": "hard_credit",
                "frequency": "q4_only",
                "period_amount": 75.0,
                "requires_activation": 1,
                "activation_done": 0,
                "caveats": "Oct–Dec: Earn 5% cash back (7,500 Ultimate Rewards points transferable to CSR!) on up to $1,500 in combined purchases via PayPal, McDonald's, Pet Shops & Vet Services, and Select Charities. Activate now for Q4 at ChaseBonus.com!",
                "sort_order": 4,
            },
            {
                "name": "DoorDash Quarterly Non-Restaurant Promo ($10/qtr)",
                "category": "Lifestyle",
                "credit_type": "hard_credit",
                "frequency": "quarterly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Up to $10 off one non-restaurant DoorDash order (grocery, convenience, DashMart, retail) each quarter with active DashPass.",
                "sort_order": 5,
            },
            {
                "name": "Transfer / Pool 5x Ultimate Rewards® to CSR",
                "category": "Travel",
                "credit_type": "lifestyle_perk",
                "frequency": "quarterly",
                "period_amount": 35.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Move your 7,500 quarterly Freedom Ultimate Rewards points onto your Chase Sapphire Reserve to unlock 1:1 airline/hotel transfer partners (Hyatt, United, etc. — turning 5% into 7.5%–10%+ effective return!).",
                "sort_order": 6,
            },
            {
                "name": "Chase Offers Monthly Activation & Redemptions",
                "category": "Lifestyle",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Check and activate targeted 5%–20% statement credit Chase Offers in the Chase app (stackable on top of your 5x rotating quarterly bonus!).",
                "sort_order": 7,
            },
        ],
    },
    "discover_it": {
        "name": "Discover it® Cash Back",
        "preset_key": "discover_it",
        "issuer": "Discover",
        "annual_fee": 0.0,
        "au_fee": 0.0,
        "au_count": 0,
        "anniversary_date": "2026-01-01",
        "color_theme": "amber",
        "earning_summary": "5% Rotating Quarterly Categories (up to $1,500 spend / $75 cash back per quarter after activation) • 1% All Else",
        "perks": [
            {
                "name": "5% Q1 Rotating: Groceries, Wholesale Clubs & Streaming ($75 Max)",
                "category": "Rotating 5%",
                "credit_type": "hard_credit",
                "frequency": "q1_only",
                "period_amount": 75.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Jan–Mar: Earn 5% cash back on up to $1,500 in combined purchases ($75 max cash back) at Grocery Stores, Wholesale Clubs, and Select Streaming. Must activate!",
                "sort_order": 1,
            },
            {
                "name": "5% Q2 Rotating: Restaurants & Home Improvement Stores ($75 Max)",
                "category": "Rotating 5%",
                "credit_type": "hard_credit",
                "frequency": "q2_only",
                "period_amount": 75.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Apr–Jun: Earn 5% cash back on up to $1,500 in combined purchases ($75 max cash back) at Restaurants and Home Improvement Stores. Must activate!",
                "sort_order": 2,
            },
            {
                "name": "5% Q3 Rotating: Gas/EV, Public Transit & Drug Stores ($75 Max)",
                "category": "Rotating 5%",
                "credit_type": "hard_credit",
                "frequency": "q3_only",
                "period_amount": 75.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Jul–Sep: Earn 5% cash back on up to $1,500 in combined purchases ($75 max cash back) at Gas Stations, EV Charging, Public Transit, and Drug Stores. Must activate!",
                "sort_order": 3,
            },
            {
                "name": "5% Q4 Rotating: Restaurants, Entertainment & Utilities ($75 Max)",
                "category": "Rotating 5%",
                "credit_type": "hard_credit",
                "frequency": "q4_only",
                "period_amount": 75.0,
                "requires_activation": 1,
                "activation_done": 0,
                "caveats": "Oct–Dec: Earn 5% cash back on up to $1,500 in combined purchases ($75 max cash back) at Restaurants, Entertainment, and Utilities. Activate now for Q4!",
                "sort_order": 4,
            },
            {
                "name": "Gift Card Redemption 5%–20% Bonus Booster",
                "category": "Lifestyle",
                "credit_type": "lifestyle_perk",
                "frequency": "quarterly",
                "period_amount": 15.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Redeem Discover Cashback Bonus for partner gift cards (Apple, Nike, Lowe's, Home Depot, Gap, Old Navy, etc.) to get an automatic 5%–20% extra value added.",
                "sort_order": 5,
            },
            {
                "name": "Discover Online Privacy Protection (90-Day Scan)",
                "category": "Lifestyle",
                "credit_type": "lifestyle_perk",
                "frequency": "quarterly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Free recurring scan in the Discover mobile app that removes your personal info from 10+ major people-search websites every 90 days.",
                "sort_order": 6,
            },
        ],
    },
    "amex_everyday": {
        "name": "Amex EveryDay® Credit Card",
        "preset_key": "amex_everyday",
        "issuer": "American Express",
        "annual_fee": 0.0,
        "au_fee": 0.0,
        "au_count": 0,
        "anniversary_date": "2026-04-01",
        "color_theme": "emerald",
        "earning_summary": "2x U.S. Supermarkets (up to $6k/yr) • 1x All Else • +20% Points Bonus when you make 20+ purchases in a billing period (2.4x Groceries / 1.2x Catch-All)",
        "perks": [
            {
                "name": "20+ Monthly Swipes = 20% Extra MR Points Bonus",
                "category": "Multiplier Bonus",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 20.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Make 20+ purchases in a billing period to earn 20% extra Membership Rewards points (turns 2x Groceries into 2.4x MR & 1x into 1.2x MR). Log 0–20 swipes in each month bubble!",
                "sort_order": 1,
            },
            {
                "name": "2x U.S. Supermarkets Annual Cap ($120 Value on $6,000 Spend)",
                "category": "Dining",
                "credit_type": "hard_credit",
                "frequency": "annual",
                "period_amount": 120.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Earn 2x Membership Rewards points (2.4x with 20-swipe bonus) on up to $6,000/yr at U.S. Supermarkets (12,000–14,400 MR points ≈ $120–$216 value).",
                "sort_order": 2,
            },
            {
                "name": "Amex Offers Monthly Statement Credits",
                "category": "Lifestyle",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Monthly check-off to enroll in targeted Amex Offers (statement credits or bonus Membership Rewards points).",
                "sort_order": 3,
            },
            {
                "name": "$0-Fee Membership Rewards® Transfer Partner Anchor",
                "category": "Travel",
                "credit_type": "lifestyle_perk",
                "frequency": "annual",
                "period_amount": 95.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Keeps all your Amex Membership Rewards points alive indefinitely with $0 annual fee and full airline/hotel transfer partner access.",
                "sort_order": 4,
            },
        ],
    },
    "amex_bce": {
        "name": "Amex Blue Cash Everyday® (BCE)",
        "preset_key": "amex_bce",
        "issuer": "American Express",
        "annual_fee": 0.0,
        "au_fee": 0.0,
        "au_count": 0,
        "anniversary_date": "2026-05-01",
        "color_theme": "sapphire",
        "earning_summary": "3% U.S. Supermarkets ($6k/yr cap) • 3% U.S. Online Retail ($6k/yr cap) • 3% U.S. Gas Stations ($6k/yr cap) • 1% All Else",
        "perks": [
            {
                "name": "Disney Bundle Monthly Credit ($7/mo = $84/yr)",
                "category": "Subscriptions",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 7.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "$7/month statement credit after spending $9.99+ on eligible Disney+, Hulu, or ESPN+ subscriptions in the U.S. Enrollment required.",
                "sort_order": 1,
            },
            {
                "name": "Home Chef Monthly Meal Credit ($15/mo = $180/yr)",
                "category": "Dining",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 15.0,
                "requires_activation": 1,
                "activation_done": 0,
                "caveats": "Up to $15/month statement credit on Home Chef meal kit subscriptions purchased online in the U.S. Enrollment required.",
                "sort_order": 2,
            },
            {
                "name": "3% U.S. Online Retail Reward Cap ($180 Max on $6,000/yr Spend)",
                "category": "Rotating 5%",
                "credit_type": "hard_credit",
                "frequency": "quarterly",
                "period_amount": 45.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Earn 3% cash back on U.S. Online Retail purchases (Amazon, Walmart.com, Target.com, eBay, Gap.com, Apple, etc.) up to $6,000/yr ($180/yr max reward, or ~$45/quarter).",
                "sort_order": 3,
            },
            {
                "name": "3% U.S. Supermarkets Reward Cap ($180 Max on $6,000/yr Spend)",
                "category": "Dining",
                "credit_type": "hard_credit",
                "frequency": "quarterly",
                "period_amount": 45.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Earn 3% cash back at U.S. Supermarkets up to $6,000/yr ($180/yr max reward, or ~$45/quarter).",
                "sort_order": 4,
            },
            {
                "name": "3% U.S. Gas Stations Reward Cap ($180 Max on $6,000/yr Spend)",
                "category": "Travel",
                "credit_type": "hard_credit",
                "frequency": "quarterly",
                "period_amount": 45.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Earn 3% cash back at U.S. Gas Stations up to $6,000/yr ($180/yr max reward, or ~$45/quarter).",
                "sort_order": 5,
            },
            {
                "name": "Amex Offers Targeted Statement Credits",
                "category": "Lifestyle",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Check and enroll in monthly Amex Offers for retail, streaming, travel, and dining statement credits.",
                "sort_order": 6,
            },
        ],
    },
    "boa_ccr": {
        "name": "Bank of America® Customized Cash Rewards",
        "preset_key": "boa_ccr",
        "issuer": "Bank of America",
        "annual_fee": 0.0,
        "au_fee": 0.0,
        "au_count": 0,
        "anniversary_date": "2026-03-01",
        "color_theme": "sapphire",
        "earning_summary": "3% Choice Category (Online Shopping [incl. Cable/Internet/Phone/Streaming], Gas/EV, Dining, Travel, Drug Stores, or Home Improvement) • 2% Grocery & Wholesale Clubs (Costco Visa!) up to $2,500 combined/qtr ($75–$131.25/qtr) • 1% All Else",
        "perks": [
            {
                "name": "3% Choice Category + 2% Grocery/Wholesale Quarterly Cap ($75 Max on $2,500/qtr Spend)",
                "category": "Rotating 5%",
                "credit_type": "hard_credit",
                "frequency": "quarterly",
                "period_amount": 75.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Earn 3% cash back in your selected Choice Category (Online Shopping [incl. Cable/Internet/Phone/Streaming], Gas/EV, Dining, Travel, Drug Stores, or Home Improvement) and 2% at Grocery Stores & Wholesale Clubs (Costco in-warehouse Visa!) up to $2,500 combined spend per quarter ($75 max/qtr at 3%, or up to $131.25/qtr with Preferred Rewards).",
                "sort_order": 1,
            },
            {
                "name": "Monthly 3% Choice Category Selection Check (Can Switch 1x/Month)",
                "category": "Multiplier Bonus",
                "credit_type": "lifestyle_perk",
                "frequency": "monthly",
                "period_amount": 5.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "You can change your 3% Choice Category once per calendar month in the Bank of America Mobile App or Online Banking (e.g., switch to Home Improvement before a Home Depot/IKEA trip, Drug Stores for CVS, Travel for Airbnb/flights, or keep on Online Shopping).",
                "sort_order": 2,
            },
            {
                "name": "Museums on Us® Free Weekend Admission (1st Full Weekend/mo)",
                "category": "Entertainment",
                "credit_type": "lifestyle_perk",
                "frequency": "monthly",
                "period_amount": 25.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Present your Bank of America Customized Cash Rewards card and photo ID on the first full weekend (Sat & Sun) of every month for free general admission at 227 museums across the U.S.",
                "sort_order": 3,
            },
            {
                "name": "BankAmeriDeals® Monthly Cash Back Offers",
                "category": "Lifestyle",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Activate targeted 5%–15% cash back deals in the Bank of America mobile app before shopping or dining (stacks on top of your 3% Choice Category and 2% Grocery/Wholesale rewards!).",
                "sort_order": 4,
            },
        ],
    },
    "boa_travel": {
        "name": "Bank of America® Travel Rewards",
        "preset_key": "boa_travel",
        "issuer": "Bank of America",
        "annual_fee": 0.0,
        "au_fee": 0.0,
        "au_count": 0,
        "anniversary_date": "2026-08-01",
        "color_theme": "sapphire",
        "earning_summary": "1.5x Unlimited Points on All Purchases (up to 2.62x with Preferred Rewards) • 3x BoA Travel Center • $0 Annual Fee & No Foreign Transaction Fees",
        "perks": [
            {
                "name": "Travel & Dining Statement Credit Redemptions",
                "category": "Travel",
                "credit_type": "hard_credit",
                "frequency": "quarterly",
                "period_amount": 25.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Redeem points (starting at 2,500 pts = $25) to erase eligible travel and dining purchases (flights, hotels, transit, baggage, tourist attractions, restaurants) within 12 months.",
                "sort_order": 1,
            },
            {
                "name": "Museums on Us® Free Weekend Admission (1st Full Weekend/mo)",
                "category": "Entertainment",
                "credit_type": "lifestyle_perk",
                "frequency": "monthly",
                "period_amount": 25.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Free general admission on the first full weekend of every month at 225+ partner museums nationwide with your BoA Travel Rewards card + ID.",
                "sort_order": 2,
            },
            {
                "name": "BankAmeriDeals® Monthly Cash Back Offers",
                "category": "Lifestyle",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Enroll in targeted 5%–15% cash back merchant offers in the BoA app.",
                "sort_order": 3,
            },
            {
                "name": "No-FTF International Visa Backup & Travel Protections",
                "category": "Travel",
                "credit_type": "lifestyle_perk",
                "frequency": "annual",
                "period_amount": 50.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "0% foreign transaction fees on all international purchases with Visa acceptance everywhere.",
                "sort_order": 4,
            },
        ],
    },
    "csr_550": {
        "name": "Chase Sapphire Reserve (Legacy $550)",
        "preset_key": "csr_550",
        "issuer": "Chase",
        "annual_fee": 550.0,
        "au_fee": 75.0,
        "au_count": 0,
        "anniversary_date": "2026-11-01",
        "color_theme": "slate",
        "earning_summary": "3x Travel & Dining • 10x Chase Dining / Hotels & Car Rentals via Portal • 1x All Else",
        "perks": [
            {
                "name": "$300 Annual Travel Credit",
                "category": "Travel",
                "credit_type": "hard_credit",
                "frequency": "anniversary",
                "period_amount": 300.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Automatically applied to eligible travel purchases each cardmember anniversary year.",
                "sort_order": 1,
            },
            {
                "name": "DoorDash Restaurant Credit ($5/mo)",
                "category": "Dining",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 5.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "$5/month promo credit on restaurant orders with active DashPass.",
                "sort_order": 2,
            },
            {
                "name": "DoorDash Grocery / Retail Promo #1 ($10/mo)",
                "category": "Lifestyle",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "First $10/month non-restaurant promo credit.",
                "sort_order": 3,
            },
            {
                "name": "DoorDash Grocery / Retail Promo #2 ($10/mo)",
                "category": "Lifestyle",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Second $10/month non-restaurant promo credit.",
                "sort_order": 4,
            },
            {
                "name": "Lyft Credit ($10/mo)",
                "category": "Travel",
                "credit_type": "hard_credit",
                "frequency": "monthly",
                "period_amount": 10.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "$10/month in-app Lyft credit.",
                "sort_order": 5,
            },
            {
                "name": "Complimentary DashPass Membership",
                "category": "Subscriptions",
                "credit_type": "lifestyle_perk",
                "frequency": "annual",
                "period_amount": 120.0,
                "requires_activation": 1,
                "activation_done": 1,
                "caveats": "Complimentary DashPass subscription ($120/yr retail value).",
                "sort_order": 6,
            },
            {
                "name": "Global Entry / TSA PreCheck / NEXUS Credit",
                "category": "Travel",
                "credit_type": "hard_credit",
                "frequency": "quadrennial",
                "period_amount": 120.0,
                "requires_activation": 0,
                "activation_done": 1,
                "caveats": "Up to $120 statement credit every 4 years.",
                "sort_order": 7,
            },
        ],
    },
}

USER_WALLET_PRESETS = [
    "csr_795",
    "chase_freedom",
    "discover_it",
    "amex_everyday",
    "amex_bce",
    "boa_ccr",
    "boa_travel",
]


# ---------------------------------------------------------------------------
# Live Website Query / Scraper Engine + Automatic Daily Sync Scheduler
# ---------------------------------------------------------------------------
def _fetch_single_issuer_source(src: dict[str, str]) -> dict[str, Any]:
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        src["url"],
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=6, context=ctx) as resp:
            raw_html = resp.read(85000).decode("utf-8", errors="ignore")
            status_code = resp.status
            title_match = re.search(
                r"<title[^>]*>(.*?)</title>", raw_html, re.IGNORECASE | re.DOTALL
            )
            page_title = (
                re.sub(r"\s+", " ", title_match.group(1)).strip()[:110]
                if title_match
                else src["card_name"]
            )
            plain = re.sub(r"<[^>]+>", " ", raw_html)
            plain = re.sub(r"\s+", " ", plain)
            snippets = []
            for pat in [
                r"5%\s+Cashback\s+Bonus[^.]{0,80}",
                r"\$[0-9,]+\s+(?:annual\s+)?travel\s+credit",
                r"3%\s+cash\s+back[^.]{0,80}",
                r"1\.5%\s+cash\s+back|1\.5\s+points",
            ]:
                m = re.search(pat, plain, re.IGNORECASE)
                if m:
                    snippets.append(m.group(0).strip())
            live_note = (
                f"Live HTTP {status_code} ({page_title})"
                + (f" — Verified: {'; '.join(snippets[:2])}" if snippets else "")
            )
            return {
                **src,
                "http_status": status_code,
                "online": 1,
                "page_title": page_title,
                "live_note": live_note,
                "last_checked": now_iso,
            }
    except Exception as exc:
        return {
            **src,
            "http_status": 0,
            "online": 0,
            "page_title": src["card_name"],
            "live_note": f"Cached rules active (remote fetch note: {str(exc)[:60]})",
            "last_checked": now_iso,
        }


def sync_all_issuer_pages(trigger_source: str = "manual") -> list[dict[str, Any]]:
    """Concurrently query all 7 official credit card issuer websites + scrape vendor glossaries."""
    with _SYNC_LOCK:
        now_utc = dt.datetime.now(dt.timezone.utc)
        today_iso = dt.date.today().isoformat()
        now_iso = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

        with ThreadPoolExecutor(max_workers=7) as pool:
            results = list(pool.map(_fetch_single_issuer_source, ISSUER_SOURCES))

        with get_db() as conn:
            vendor_engine.ensure_vendor_tables(conn)
            for r in results:
                conn.execute(
                    """
                    INSERT INTO issuer_sync_status (
                        preset_key, card_name, issuer, url, activation_url,
                        http_status, online, page_title, verified_rules, live_note, last_checked
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(preset_key) DO UPDATE SET
                        card_name = excluded.card_name,
                        issuer = excluded.issuer,
                        url = excluded.url,
                        activation_url = excluded.activation_url,
                        http_status = excluded.http_status,
                        online = excluded.online,
                        page_title = excluded.page_title,
                        verified_rules = excluded.verified_rules,
                        live_note = excluded.live_note,
                        last_checked = excluded.last_checked
                    """,
                    (
                        r["preset_key"],
                        r["card_name"],
                        r["issuer"],
                        r["url"],
                        r["activation_url"],
                        r["http_status"],
                        r["online"],
                        r["page_title"],
                        r["verified_rules"],
                        r["live_note"],
                        r["last_checked"],
                    ),
                )
            vendor_engine.scrape_live_issuer_vendors(conn)
            conn.execute(
                "INSERT OR REPLACE INTO sync_metadata (key, value) VALUES ('last_sync_date', ?)",
                (today_iso,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO sync_metadata (key, value) VALUES ('last_sync_utc', ?)",
                (now_iso,),
            )
            conn.execute(
                "INSERT OR REPLACE INTO sync_metadata (key, value) VALUES ('last_sync_trigger', ?)",
                (trigger_source,),
            )
            conn.commit()
        return results


def maybe_sync_on_first_open_of_day() -> bool:
    """
    Check the `last_sync_date` flag in SQLite `sync_metadata`.
    If today's date (YYYY-MM-DD) does not match `last_sync_date` (i.e. first time
    the website is opened today), run a live vendor sync and update the flag;
    otherwise skip immediately.
    """
    today_iso = dt.date.today().isoformat()
    with get_db() as conn:
        vendor_engine.ensure_vendor_tables(conn)
        row = conn.execute(
            "SELECT value FROM sync_metadata WHERE key = 'last_sync_date'"
        ).fetchone()
        last_sync_date = row["value"] if row else ""
        v_count = conn.execute("SELECT COUNT(*) FROM scraped_vendors").fetchone()[0]

    if last_sync_date != today_iso or v_count == 0:
        sync_all_issuer_pages(trigger_source="first_open_of_day")
        return True
    return False


# ---------------------------------------------------------------------------
# Section 1: Currently Valid Merchant & Spend Advisor (Zero Expired Deals!)
# ---------------------------------------------------------------------------
BOA_CCR_CHOICE_OPTIONS: list[dict[str, str]] = [
    {"key": "home_improvement", "label": "Home Improvement & Furnishings"},
    {"key": "online_shopping", "label": "Online Shopping (incl. Cable, Internet, Phone & Streaming)"},
    {"key": "gas", "label": "Gas & EV Charging Stations"},
    {"key": "dining", "label": "Dining (Restaurants & Takeout)"},
    {"key": "travel", "label": "Travel (Flights, Hotels, Airbnb/Vrbo & Transit)"},
    {"key": "drug_stores", "label": "Drug Stores & Pharmacies"},
]


def set_boa_choice_category(conn: sqlite3.Connection, choice_key: str) -> str:
    valid_keys = {opt["key"]: opt["label"] for opt in BOA_CCR_CHOICE_OPTIONS}
    if choice_key not in valid_keys:
        choice_key = "home_improvement"
    label = valid_keys[choice_key]
    conn.execute(
        "INSERT OR REPLACE INTO sync_metadata (key, value) VALUES ('boa_ccr_choice_category', ?)",
        (choice_key,),
    )
    new_summary = (
        f"★ Active 3% Choice Category: {label} • 2% Automatic at Grocery Stores & Wholesale Clubs (Costco Visa!) "
        "up to $2,500 combined/qtr ($75–$131.25/qtr) • 1% All Else"
    )
    conn.execute(
        "UPDATE cards SET earning_summary = ? WHERE preset_key = 'boa_ccr'",
        (new_summary,),
    )
    conn.execute(
        """
        UPDATE perks
        SET name = ?, caveats = ?
        WHERE card_id IN (SELECT id FROM cards WHERE preset_key = 'boa_ccr')
          AND sort_order = 1
        """,
        (
            f"3% Choice ({label}) + 2% Grocery/Wholesale Cap ($75 Max on $2,500/qtr)",
            f"Currently selected 3% Choice Category: {label}. Earns 3% in your selected category + 2% at Grocery Stores & Wholesale Clubs (Costco Visa!) up to $2,500 combined spend per quarter ($75 max/qtr at 3%). You can switch your 3% category once per calendar month in the BoA app or using the selector at the top of the app!",
        ),
    )
    conn.commit()
    return choice_key


def build_merchant_advisor(
    today: dt.date,
    perks_by_preset: dict[str, list[dict[str, Any]]],
    boa_choice: str = "home_improvement",
) -> list[dict[str, Any]]:
    quarter = (today.month - 1) // 3 + 1
    cur_month_name = MONTH_NAMES[today.month - 1]
    half_label = "H1 (Jan–Jun)" if today.month <= 6 else "H2 (Jul–Dec)"
    boa_choice_label = dict((o["key"], o["label"]) for o in BOA_CCR_CHOICE_OPTIONS).get(
        boa_choice, "Home Improvement & Furnishings"
    )

    def find_perk_info(preset_key: str, name_substr: str) -> dict[str, Any] | None:
        for p in perks_by_preset.get(preset_key, []):
            if name_substr.lower() in p["name"].lower():
                periods = p.get("periods", [])
                cur_p = next(
                    (x for x in periods if x.get("is_current")), periods[0] if periods else None
                )
                return {
                    "perk_id": p["id"],
                    "card_id": p["card_id"],
                    "perk_name": p["name"],
                    "requires_activation": bool(p["requires_activation"]),
                    "activation_done": bool(p["activation_done"]),
                    "remaining_amount": cur_p["remaining_amount"]
                    if cur_p
                    else p["annual_remaining"],
                    "max_amount": cur_p["max_amount"] if cur_p else p["annual_max"],
                    "period_label": cur_p["label"] if cur_p else "Annual",
                    "period_key": cur_p["key"] if cur_p else "ANNUAL",
                }
        return None

    discover_q_perk = find_perk_info("discover_it", f"Q{quarter} Rotating")
    freedom_q_perk = find_perk_info("chase_freedom", f"Q{quarter} Rotating")
    csr_travel = find_perk_info("csr_795", "Annual Travel Credit")
    csr_edit = find_perk_info("csr_795", "The Edit")
    csr_dining = find_perk_info("csr_795", "Exclusive Tables")
    csr_stubhub = find_perk_info("csr_795", "StubHub")
    csr_lyft = find_perk_info("csr_795", "Lyft")
    bce_online = find_perk_info("amex_bce", "Online Retail")
    bce_grocery = find_perk_info("amex_bce", "Supermarkets")
    bce_gas = find_perk_info("amex_bce", "Gas Stations")
    bce_disney = find_perk_info("amex_bce", "Disney Bundle")
    boa_ccr_cap = find_perk_info("boa_ccr", "3% Choice") or find_perk_info("boa_ccr", "Quarterly Cap")
    boa_museums = find_perk_info("boa_ccr", "Museums on Us") or find_perk_info(
        "boa_travel", "Museums on Us"
    )

    items: list[dict[str, Any]] = [
        {
            "id": "amazon",
            "icon": "📦",
            "title": "Amazon.com & Online Shopping",
            "merchants": "Amazon, Whole Foods on Amazon, Target.com, Walmart.com, BestBuy.com, Chewy.com, Wayfair.com",
            "group": "shopping",
            "validity_badge": (
                "🔥 Active Q2 5x UR"
                if quarter == 2
                else (
                    "★ Active 3% BoA + 3% BCE"
                    if boa_choice == "online_shopping"
                    else f"Valid Now ({today.year})"
                )
            ),
            "is_rotating_now": quarter == 2 or boa_choice == "online_shopping",
            "best_card": (
                "Chase Freedom® (Active Q2 5x Amazon.com!)"
                if quarter == 2
                else (
                    "Amex Blue Cash Everyday® (3% Online Retail) & BoA Customized Cash Rewards (★ Active 3% Online Shopping!)"
                    if boa_choice == "online_shopping"
                    else "Amex Blue Cash Everyday® (3% U.S. Online Retail)"
                )
            ),
            "best_rate": (
                "5x UR (≈7.5%+ via CSR)"
                if quarter == 2
                else (
                    "3% Cash Back (Both Cards Active = $16,000/yr Cap!)"
                    if boa_choice == "online_shopping"
                    else "3% Cash Back (Up to $6,000/yr)"
                )
            ),
            "runner_up": (
                "Chase Freedom (Active Q4 5x UR on any online store that takes PayPal!)"
                if quarter == 4
                else f"BoA Travel Rewards (1.5x–2.62x) • (BoA Customized Cash is currently set to 3% {boa_choice_label})"
            ),
            "why": (
                "🔥 ACTIVE NOW (Apr–Jun): Chase Freedom earns 5x Ultimate Rewards (up to $1,500) at Amazon.com & Whole Foods!"
                if quarter == 2
                else (
                    "Both Amex BCE (up to $6,000/yr) and your Bank of America Customized Cash Rewards (currently set to 3% Online Shopping, up to $2,500/qtr) earn 3% cash back on online purchases!"
                    if boa_choice == "online_shopping"
                    else f"Amex BCE earns 3% cash back automatically on U.S. Online Retail purchases up to $6,000/year! Plus in Q4 (Oct–Dec), any online store where you check out with PayPal earns 5x UR on Chase Freedom! (Your BoA Customized Cash 3% category is currently set to '{boa_choice_label}'.)"
                )
            ),
            "special_activation": "Check targeted 'Pay with 1 Point' promos on Amazon before checkout (linking Amex MR, Chase UR, or Discover Cashback for 1 point often triggers $15–$50 off!).",
            "portal_url": "https://global.americanexpress.com/card-benefits",
            "linked_perk": freedom_q_perk if quarter == 2 else bce_online,
        },
        {
            "id": "tv_internet_phone_utilities",
            "icon": "📺",
            "title": "TV, Internet, Phone & Utility Bills",
            "merchants": "Comcast/Xfinity, Spectrum, Verizon, AT&T, T-Mobile, PG&E, ConEd, Electric, Gas, Water, YouTube TV",
            "group": "bills",
            "validity_badge": (
                f"🔥 Active Q{quarter} Bonus"
                if quarter in (1, 4)
                else (
                    "★ Active 3% BoA Choice"
                    if boa_choice == "online_shopping"
                    else f"Valid Now ({cur_month_name})"
                )
            ),
            "is_rotating_now": quarter in (1, 4) or boa_choice == "online_shopping",
            "best_card": (
                "Discover it® Cash Back (Active Q4 5% Utilities!)"
                + (
                    " & BoA Customized Cash (★ Active 3% Cable/Internet/Phone!)"
                    if boa_choice == "online_shopping"
                    else ""
                )
                if quarter == 4
                else (
                    "Bank of America® Customized Cash Rewards (★ Active 3% Cable, Internet, Phone & Streaming!)"
                    if boa_choice == "online_shopping"
                    else "Bank of America® Travel Rewards (1.5x–2.62x) or Amex EveryDay® (1.2x MR)"
                )
            ),
            "best_rate": (
                "5% Cash Back (Active Q4)"
                if quarter == 4
                else ("3% Cash Back (★ Active BoA Choice)" if boa_choice == "online_shopping" else "1.5x–2.62x BoA / 1.2x MR")
            ),
            "runner_up": (
                f"Chase Freedom (Active Q4 5x UR if bill accepts PayPal!) • (BoA 3% is currently set to {boa_choice_label})"
                if quarter == 4
                else f"Amex BCE ($7/mo Disney+) • (Switch BoA 3% from {boa_choice_label} → Online Shopping for 3% on Cable/Internet/Phone!)"
            ),
            "why": (
                "🔥 ACTIVE RIGHT NOW (Oct–Dec): Discover it earns 5% cash back on Utilities (electric, gas, water, internet, cable, phone bills) up to $1,500! Pre-pay bills before Dec 31 to max out your $75 cash back."
                if quarter == 4
                else (
                    "Your Bank of America Customized Cash Rewards 3% Choice Category is set to 'Online Shopping', which officially includes Cable, Internet, Phone Plans, and Streaming Subscriptions up to $2,500/quarter!"
                    if boa_choice == "online_shopping"
                    else f"Use BoA Travel Rewards (1.5x–2.62x) or Amex EveryDay (1.2x MR w/ 20 swipes). Tip: If you switch your BoA Customized Cash 3% category from '{boa_choice_label}' to 'Online Shopping', Cable, Internet & Phone bills earn 3%!"
                )
            ),
            "special_activation": (
                "⚠️ MUST ACTIVATE Discover Q4 5% bonus before paying utility/TV bills! Also check Chase Offers, Amex Offers & BankAmeriDeals (Verizon, AT&T, T-Mobile, and Fiber/Cable bills frequently have $25–$50 back targeted offers)."
                if quarter == 4
                else "Check Chase Offers, Amex Offers & BankAmeriDeals for targeted $25–$50 telecom statement credits."
            ),
            "portal_url": "https://www.discover.com/credit-cards/cash-back/cashback-calendar.html",
            "linked_perk": discover_q_perk if quarter in (1, 4) else boa_ccr_cap,
        },
        {
            "id": "cvs_drugstores",
            "icon": "💊",
            "title": "CVS, Walgreens & Drugstores / Pharmacy",
            "merchants": "CVS Pharmacy, Walgreens, Duane Reade, Rite Aid, Local Pharmacies",
            "group": "shopping",
            "validity_badge": (
                "🔥 Active Q4 5x PayPal"
                if quarter == 4
                else (
                    "🔥 Active Q3 5%"
                    if quarter == 3
                    else ("★ Active 3% BoA Choice" if boa_choice == "drug_stores" else f"Valid Now ({today.year})")
                )
            ),
            "is_rotating_now": quarter in (3, 4) or boa_choice == "drug_stores",
            "best_card": (
                "Discover it® Cash Back (Active Q3 5% Drug Stores!)"
                if quarter == 3
                else (
                    "Bank of America® Customized Cash Rewards (★ Active 3% Drug Stores!)"
                    if boa_choice == "drug_stores"
                    else (
                        "Chase Freedom® (Active Q4 5x UR via PayPal!) or Amex BCE (3% Online at CVS.com)"
                        if quarter == 4
                        else "Amex Blue Cash Everyday® (3% Online at CVS.com/Walgreens.com) or BoA Travel Rewards (1.5x–2.62x In-Store)"
                    )
                )
            ),
            "best_rate": (
                "5% Cash Back (Active Q3)"
                if quarter == 3
                else (
                    "3% Cash Back (★ Active BoA Choice)"
                    if boa_choice == "drug_stores"
                    else ("5x UR via PayPal (Q4) / 3% Online" if quarter == 4 else "3% Online / 1.5x–2.62x In-Store")
                )
            ),
            "runner_up": (
                "Chase Freedom (5x UR via PayPal in Q4) or Amex BCE (3% Online)"
                if boa_choice == "drug_stores"
                else f"BoA Customized Cash Rewards (currently set to 3% {boa_choice_label} — can switch to Drug Stores 1x/mo)"
            ),
            "why": (
                "ACTIVE NOW: Discover it gives 5% at Drug Stores up to $1,500."
                if quarter == 3
                else (
                    "Your Bank of America Customized Cash Rewards 3% Choice Category is set to 'Drug Stores & Pharmacies', earning 3% cash back in-store & online at CVS, Walgreens, and pharmacies up to $2,500/qtr!"
                    if boa_choice == "drug_stores"
                    else (
                        f"🔥 ACTIVE RIGHT NOW (Oct–Dec): Chase Freedom earns 5x Ultimate Rewards on PayPal — and both CVS and Walgreens accept PayPal in-store (via QR code in the PayPal app) and online at CVS.com/Walgreens.com! (Your BoA Customized Cash 3% category is currently set to '{boa_choice_label}'.)"
                        if quarter == 4
                        else f"Order online at CVS.com/Walgreens.com with Amex BCE for 3%, or swipe BoA Travel Rewards (1.5x–2.62x) in-store. (Or switch your BoA Customized Cash 3% category from '{boa_choice_label}' to 'Drug Stores'.)"
                    )
                )
            ),
            "special_activation": "Activate Chase Freedom Q4 5x (PayPal) at ChaseBonus.com, and check Chase Offers & BankAmeriDeals for targeted 10% back CVS/Walgreens statement credits!",
            "portal_url": "https://www.chasebonus.com/",
            "linked_perk": discover_q_perk if quarter == 3 else (freedom_q_perk if quarter == 4 else boa_ccr_cap),
        },
        {
            "id": "ebay",
            "icon": "🏷️",
            "title": "eBay, PayPal Checkout & Online Marketplaces",
            "merchants": "eBay.com, PayPal Checkout, Etsy, Poshmark, Mercari, Back Market",
            "group": "shopping",
            "validity_badge": "🔥 Active Q4 5x PayPal!" if quarter == 4 else f"Valid Now ({today.year})",
            "is_rotating_now": quarter == 4 or boa_choice == "online_shopping",
            "best_card": (
                "Chase Freedom® (Active Q4 5x UR on PayPal!) → then Amex BCE (3% Online)"
                if quarter == 4
                else (
                    "Bank of America® Customized Cash Rewards (★ Active 3% Online Shopping!) & Amex BCE (3%)"
                    if boa_choice == "online_shopping"
                    else "Amex Blue Cash Everyday® (3% U.S. Online Retail)"
                )
            ),
            "best_rate": "5x Ultimate Rewards (Active Q4 PayPal)" if quarter == 4 else "3% Cash Back",
            "runner_up": (
                "BoA Customized Cash Rewards (★ Active 3% Online Shopping)"
                if boa_choice == "online_shopping"
                else f"Amex BCE (3% Online Retail) or BoA Travel Rewards (1.5x–2.62x)"
            ),
            "why": (
                "🔥 ACTIVE RIGHT NOW (Oct–Dec): Chase Freedom earns 5% / 5x Ultimate Rewards (worth ~7.5%–10% via CSR!) on all PayPal purchases up to $1,500 — making eBay, Etsy, and any online checkout with PayPal an automatic 5x UR win! After $1,500, use Amex BCE (3%)."
                if quarter == 4
                else "Amex BCE earns 3% cash back on eligible U.S. online retail checkouts (and BoA Customized Cash earns 3% when 'Online Shopping' is selected)!"
            ),
            "special_activation": (
                "⚠️ MUST ACTIVATE Chase Freedom Q4 5x at ChaseBonus.com before checking out with PayPal on eBay! Also check Amex Offers & Chase Offers for targeted eBay/PayPal statement credits."
                if quarter == 4
                else "Check Amex Offers, Chase Offers & BankAmeriDeals before checkout for targeted eBay promo credits."
            ),
            "portal_url": "https://www.chasebonus.com/",
            "linked_perk": freedom_q_perk if quarter == 4 else bce_online,
        },
        {
            "id": "gap_apparel",
            "icon": "👕",
            "title": "Gap, Old Navy, Banana Republic & Apparel Stores",
            "merchants": "Gap, Old Navy, Banana Republic, Athleta, Macy's, Nordstrom, Bloomingdale's, Saks, Nike, Lululemon",
            "group": "shopping",
            "validity_badge": "🔥 Active Q4 5x via PayPal" if quarter == 4 else f"Valid Now ({today.year})",
            "is_rotating_now": quarter == 4,
            "best_card": (
                "Chase Freedom® (Active Q4 5x UR via PayPal Checkout!) or Amex Blue Cash Everyday® (3% Online)"
                if quarter == 4
                else (
                    "Amex Blue Cash Everyday® (3% Online) & BoA Customized Cash (★ Active 3% Online Shopping!)"
                    if boa_choice == "online_shopping"
                    else "Amex Blue Cash Everyday® (3% Online) / Discover Gift Cards (10%–20% Bonus)"
                )
            ),
            "best_rate": "5x UR via PayPal (Q4) / 3% Online + Gift Card Stack" if quarter == 4 else "3% Online + 10%–20% Gift Card Bonus",
            "runner_up": "Discover Cashback Bonus Gift Cards (10%–20% extra value on Gap/Old Navy/Banana Republic/Nike)",
            "why": (
                "Right now in Q4 (Oct–Dec), checking out online with PayPal at Gap, Old Navy, Nike, Nordstrom, or Macy's earns 5x Ultimate Rewards on Chase Freedom! Or pay directly online with Amex BCE (3%), or redeem Discover Cashback for Gap/Old Navy gift cards (+10%–20% bonus)!"
                if quarter == 4
                else "Ordering online at Gap.com, OldNavy.com, or Nordstrom.com triggers 3% on Amex BCE. Or redeem Discover Cashback for Gap/Old Navy gift cards for a 10%–20% bonus!"
            ),
            "special_activation": "1) Click through 'Shop Through Chase' before checkout for extra UR points, 2) Use Chase Freedom via PayPal in Q4 (5x UR), and 3) Check Chase Offers & BankAmeriDeals for 10%–15% Gap/Old Navy promos!",
            "portal_url": "https://www.chasebonus.com/",
            "linked_perk": freedom_q_perk if quarter == 4 else bce_online,
        },
        {
            "id": "hotels",
            "icon": "🏨",
            "title": "Hotels, Resorts & Vacation Rentals",
            "merchants": "The Edit by Chase Travel, Marriott, Hyatt, Hilton, IHG, Airbnb, Vrbo, Expedia",
            "group": "travel",
            "validity_badge": "★ 3% BoA Travel Active + CSR" if boa_choice == "travel" else f"Valid Now ({today.year})",
            "is_rotating_now": boa_choice == "travel",
            "best_card": (
                "Chase Sapphire Reserve® (8x Portal / 4x Direct Hotels) + BoA Customized Cash (★ Active 3% Travel for Airbnb/Vrbo!)"
                if boa_choice == "travel"
                else "Chase Sapphire Reserve®"
            ),
            "best_rate": "8x Chase Travel / 4x Direct + $800 Credits",
            "runner_up": (
                "BoA Customized Cash Rewards (★ Active 3% Travel — covers Airbnb & Vrbo!)"
                if boa_choice == "travel"
                else "Chase Freedom (5x Chase Travel) or BoA Travel Rewards (3x BoA Portal)"
            ),
            "why": "CSR earns 8x points on hotels booked via Chase Travel and 4x when booked directly with the hotel, plus triggers your $300 Annual Travel Credit and up to $500 ($250 × 2) for 2-night prepaid bookings on The Edit by Chase Travel.",
            "special_activation": "Activate complimentary IHG One Rewards Platinum Elite status in Chase Benefits. Note: Airbnb & Vrbo are excluded from CSR's 4x Direct Hotels, but DO trigger CSR's $300 Travel Credit (and earn 3% on BoA Customized Cash if 'Travel' is selected)!",
            "portal_url": "https://ultimaterewards.chase.com/",
            "linked_perk": csr_edit,
        },
        {
            "id": "rideshare_transit",
            "icon": "🚗",
            "title": "Rideshare (Lyft & Uber), Parking, Tolls & Transit",
            "merchants": "Lyft, Uber, Subway/Metro, Commuter Rail, Parking Garages, E-ZPass / FasTrak",
            "group": "travel",
            "validity_badge": f"Valid in {cur_month_name}",
            "is_rotating_now": quarter == 3,
            "best_card": (
                "Chase Sapphire Reserve® ($10/mo Lyft Credit) + Discover it® (Active Q3 5% Transit)"
                if quarter == 3
                else "Chase Sapphire Reserve® ($10/mo Lyft Credit + $300 Travel Credit)"
            ),
            "best_rate": "$10/mo Lyft Credit + Travel Credit",
            "runner_up": (
                "BoA Customized Cash Rewards (★ Active 3% Travel) or Chase Freedom (5x Lyft)"
                if boa_choice == "travel"
                else "Chase Freedom (5x Lyft) or BoA Travel Rewards (1.5x–2.62x)"
            ),
            "why": f"CSR gives you an active $10 statement credit in {cur_month_name} on Lyft rides ($120/yr) AND automatically applies your $300 Annual Travel Credit to Uber, Lyft, subways, parking, and tolls.",
            "special_activation": "Set your Chase Sapphire Reserve as the default payment method inside the Lyft app to trigger the $10/month in-app credit.",
            "portal_url": "https://www.lyft.com/",
            "linked_perk": csr_lyft,
        },
        {
            "id": "grocery_supermarkets",
            "icon": "🛒",
            "title": "Grocery Stores & U.S. Supermarkets",
            "merchants": "Whole Foods, Trader Joe's, Safeway, Kroger, Publix, ALDI, Wegmans, H-E-B, ShopRite, Vons",
            "group": "daily",
            "validity_badge": "🔥 Active Q1 5x + 5%" if quarter == 1 else f"Valid Now ({today.year})",
            "is_rotating_now": quarter == 1,
            "best_card": (
                "Chase Freedom® (Active Q1 5x UR Groceries!) & Discover it® (Active Q1 5% Groceries!)"
                if quarter == 1
                else "Amex Blue Cash Everyday® (3% Cash) or Amex EveryDay® (2.4x MR)"
            ),
            "best_rate": "5x UR + 5% Cash (Active Q1)" if quarter == 1 else "3% Cash or 2.4x MR (2% BoA Backup)",
            "runner_up": f"BoA Customized Cash Rewards (2% Automatic Grocery regardless of 3% choice) + CSR $20 {cur_month_name} DoorDash Promos",
            "why": (
                "ACTIVE NOW: Both Chase Freedom (5x UR up to $1,500) and Discover it (5% cash back up to $1,500) earn 5% at Grocery Stores in Q1!"
                if quarter == 1
                else "Right now, Amex Blue Cash Everyday earns 3% cash back (up to $6k/yr), Amex EveryDay earns 2x–2.4x MR (with 20+ monthly swipes, up to $6k/yr), and Bank of America Customized Cash Rewards automatically earns 2% at Grocery Stores regardless of your 3% Choice Category!"
            ),
            "special_activation": f"Don't forget your TWO $10 {cur_month_name} DoorDash non-restaurant credits on CSR ($20/mo total) + $10 this quarter on Chase Freedom — use them for free grocery/DashMart pickup!",
            "portal_url": "https://www.americanexpress.com/us/credit-cards/card/blue-cash-everyday/",
            "linked_perk": freedom_q_perk if quarter == 1 else bce_grocery,
        },
        {
            "id": "gas_ev",
            "icon": "⛽",
            "title": "Gas Stations & EV Charging",
            "merchants": "Shell, Chevron, Exxon, Mobil, Gulf, Hess, Murphy USA, Costco Gas (Visa), Tesla Supercharger",
            "group": "daily",
            "validity_badge": (
                "🔥 Active Q3 5x + 5%"
                if quarter == 3
                else ("★ Active 3% BoA + 3% BCE" if boa_choice == "gas" else f"Valid Now ({today.year})")
            ),
            "is_rotating_now": quarter == 3 or boa_choice == "gas",
            "best_card": (
                "Chase Freedom® (Active Q3 5x Gas & EV!) & Discover it® (Active Q3 5% Gas & EV!)"
                if quarter == 3
                else (
                    "Amex Blue Cash Everyday® (3% Gas) & BoA Customized Cash Rewards (★ Active 3% Gas — Works at Costco Gas!)"
                    if boa_choice == "gas"
                    else "Amex Blue Cash Everyday® (3% U.S. Gas Stations)"
                )
            ),
            "best_rate": "5x UR + 5% Cash (Active Q3)" if quarter == 3 else "3% Cash Back Year-Round",
            "runner_up": (
                "BoA Customized Cash Rewards (★ Active 3% Gas Visa at Costco Gas!)"
                if boa_choice == "gas"
                else f"BoA Travel Rewards (1.5x–2.62x Visa at Costco Gas) • (BoA Customized Cash is currently set to 3% {boa_choice_label})"
            ),
            "why": (
                "ACTIVE NOW: Both Chase Freedom (5x UR) and Discover it (5% cash back) earn 5% on Gas Stations & EV Charging in Q3!"
                if quarter == 3
                else (
                    "Both Amex BCE (up to $6,000/yr) and your Bank of America Customized Cash Rewards (currently set to 3% Gas & EV Charging, including Costco Gas!) earn 3% cash back!"
                    if boa_choice == "gas"
                    else f"Right now, Amex Blue Cash Everyday earns 3% cash back at U.S. stand-alone gas stations (up to $6,000/yr). For Costco Gas (Visa-only), swipe BoA Travel Rewards (1.5x–2.62x) or switch BoA Customized Cash from '{boa_choice_label}' to 'Gas' for 3%!"
                )
            ),
            "special_activation": "Amex Official Terms exclude warehouse gas (Costco/Sam's). For Costco Gas (Visa-only), use BoA Travel Rewards (1.5x–2.62x) or Chase Freedom (5x UR in Q3)!",
            "portal_url": "https://www.americanexpress.com/us/credit-cards/card/blue-cash-everyday/",
            "linked_perk": freedom_q_perk if quarter == 3 else bce_gas,
        },
        {
            "id": "restaurants_dining",
            "icon": "🍽️",
            "title": "Restaurants, Takeout, Bars & Food Delivery",
            "merchants": "Local Restaurants, OpenTable Exclusive Tables, DoorDash, Caviar, Uber Eats, Grubhub, McDonald's",
            "group": "daily",
            "validity_badge": f"🔥 Active Q{quarter} 5%" if quarter in (2, 4) else f"Valid Now ({today.year})",
            "is_rotating_now": quarter in (2, 4) or boa_choice == "dining",
            "best_card": (
                f"Discover it® (Active Q{quarter} 5% Restaurants!) + CSR (3x UR & Credits)"
                + (" + Chase Freedom (5x McDonald's/PayPal!)" if quarter == 4 else "")
                if quarter in (2, 4)
                else (
                    "Chase Sapphire Reserve® (3x UR Dining) & BoA Customized Cash (★ Active 3% Dining!)"
                    if boa_choice == "dining"
                    else "Chase Sapphire Reserve® (3x Ultimate Rewards on Worldwide Dining)"
                )
            ),
            "best_rate": f"5% Cash Back (Q{quarter}) + 3x UR" if quarter in (2, 4) else "3x Ultimate Rewards",
            "runner_up": f"CSR $150 {half_label} Exclusive Tables + $15 {cur_month_name} DoorDash Promo",
            "why": (
                f"🔥 ACTIVE RIGHT NOW in Q{quarter}: Discover it earns 5% cash back at Restaurants up to $1,500"
                + (" (and Chase Freedom earns 5x UR at McDonald's & via PayPal food delivery in Q4!)" if quarter == 4 else "")
                + f". Also use CSR for your $150 {half_label} OpenTable Exclusive Tables credit, $15 {cur_month_name} DoorDash credit, and 3x UR worldwide."
                if quarter in (2, 4)
                else f"CSR earns 3x Ultimate Rewards on dining & food delivery worldwide, plus stack your CSR $150 {half_label} OpenTable credit and $15 {cur_month_name} DoorDash credit."
            ),
            "special_activation": (
                f"1) Activate Discover Q{quarter} 5% for Restaurants; 2) Activate CSR $150 Exclusive Tables Dining Credit in Chase Benefits; 3) Toggle your $15 {cur_month_name} CSR DoorDash promo at checkout."
            ),
            "portal_url": "https://ultimaterewards.chase.com/",
            "linked_perk": discover_q_perk if quarter in (2, 4) else csr_dining,
        },
        {
            "id": "entertainment_events",
            "icon": "🎟️",
            "title": "Concerts, Sports, Movies, Theme Parks & Events",
            "merchants": "StubHub, viagogo, Ticketmaster, SeatGeek, AMC/Regal Movie Theaters, Theme Parks, Zoos",
            "group": "shopping",
            "validity_badge": "🔥 Active Q4 5% + $150 Credit" if quarter == 4 else ("🔥 Active Q3 5x UR" if quarter == 3 else f"Valid in {half_label}"),
            "is_rotating_now": quarter in (3, 4),
            "best_card": (
                "Chase Sapphire Reserve® ($150 StubHub Credit) → then Discover it® (Active Q4 5% Entertainment!)"
                if quarter == 4
                else (
                    "Chase Sapphire Reserve® ($150 StubHub Credit) → then Chase Freedom® (Active Q3 5x Live Entertainment & Movies!)"
                    if quarter == 3
                    else "Chase Sapphire Reserve® ($150 StubHub Credit) → then BoA Travel Rewards (1.5x–2.62x)"
                )
            ),
            "best_rate": "$150 Credit + 5% Q4 Cash Back" if quarter == 4 else ("$150 Credit + 5x UR Q3" if quarter == 3 else "$150 StubHub Credit + 1.5x"),
            "runner_up": "BoA Customized Cash Rewards & BoA Travel Rewards (Free Museums on Us 1st full weekend/mo)",
            "why": (
                f"First use your CSR's $150 {half_label} StubHub/viagogo credit. For Ticketmaster, movie theaters, theme parks, and live entertainment right now in Q4 (Oct–Dec), Discover it gives 5% cash back (and Chase Freedom gives 5x UR if checking out with PayPal)!"
                if quarter == 4
                else f"Use your CSR's $150 {half_label} StubHub/viagogo statement credit first!"
            ),
            "special_activation": "⚠️ Must click 'Activate' on the CSR StubHub/viagogo perk in Chase Benefits BEFORE buying tickets"
            + ("! Also activate Discover Q4 5% for Entertainment." if quarter == 4 else "!"),
            "portal_url": "https://ultimaterewards.chase.com/",
            "linked_perk": csr_stubhub,
        },
        {
            "id": "flights_airlines",
            "icon": "✈️",
            "title": "Flights, Airlines & Baggage Fees",
            "merchants": "United, Delta, American, Southwest, JetBlue, International Airlines, Chase Travel",
            "group": "travel",
            "validity_badge": f"Valid Now ({today.year})",
            "is_rotating_now": False,
            "best_card": "Chase Sapphire Reserve®",
            "best_rate": "8x Chase Travel / 4x Direct Flights",
            "runner_up": (
                "BoA Customized Cash Rewards (★ Active 3% Travel) or Chase Freedom (5x Chase Travel)"
                if boa_choice == "travel"
                else "Chase Freedom (5x Chase Travel) or BoA Travel Rewards (3x BoA Center)"
            ),
            "why": "CSR earns 8x through Chase Travel or 4x booked directly with any airline, triggers your $300 Travel Credit, and includes $500/ticket Trip Delay Reimbursement (after 6 hours), Baggage Delay, and Priority Pass / Sapphire Lounge access.",
            "special_activation": "Automatic — no activation required for 4x direct flights or $300 travel credit. Ensure Priority Pass Select membership is enrolled for airport lounge access.",
            "portal_url": "https://ultimaterewards.chase.com/",
            "linked_perk": csr_travel,
        },
        {
            "id": "wholesale_costco",
            "icon": "🏬",
            "title": "Wholesale Clubs (Costco, Sam's Club, BJ's) & Big-Box",
            "merchants": "Costco Wholesale, Costco.com, Sam's Club, BJ's Wholesale, Target, Walmart",
            "group": "shopping",
            "validity_badge": "🔥 Active Q1 5%" if quarter == 1 else f"Valid Now ({today.year})",
            "is_rotating_now": quarter == 1,
            "best_card": (
                "Discover it® (Active Q1 5% Wholesale Clubs) & BoA Customized Cash Rewards (2% Costco Visa!)"
                if quarter == 1
                else "Bank of America® Customized Cash Rewards (2% Automatic at Costco & Wholesale Clubs!)"
            ),
            "best_rate": "5% (Q1) / 2% Costco Visa In-Club" if quarter == 1 else "2% In-Club (up to 3.5%) / 3% Online (Amex BCE)",
            "runner_up": "Amex Blue Cash Everyday (3% Online at Walmart.com & Target.com)",
            "why": (
                "ACTIVE NOW: Discover it earns 5% at Wholesale Clubs up to $1,500, and Bank of America Customized Cash Rewards (a Visa!) earns 2% automatic cash back in-warehouse at Costco!"
                if quarter == 1
                else "Because Bank of America Customized Cash Rewards is a Visa and automatically earns 2% cash back at Wholesale Clubs (regardless of which 3% Choice Category you have selected, up to $2,500 combined quarterly spend), it is your #1 card inside Costco! When ordering online at Walmart.com or Target.com, use Amex BCE for 3% U.S. Online Retail!"
            ),
            "special_activation": "Amex Official Exclusion: In-store Walmart, Target, and BJ's do NOT count as 3% Supermarkets on Amex BCE — swipe BoA Customized Cash Rewards (2% Wholesale Clubs) at Costco/BJ's/Sam's in-store, or order online at Walmart.com/Target.com with Amex BCE for 3%!",
            "portal_url": "https://www.bankofamerica.com/credit-cards/products/cash-back-credit-card/",
            "linked_perk": discover_q_perk if quarter == 1 else boa_ccr_cap,
        },
        {
            "id": "home_improvement",
            "icon": "🔨",
            "title": "Home Improvement, Hardware & Furniture",
            "merchants": "Home Depot, Lowe's, Ace Hardware, Menards, IKEA, Wayfair.com, Sherwin-Williams, Contractors",
            "group": "shopping",
            "validity_badge": (
                "🔥 Active Q2 5% + ★ 3% BoA"
                if quarter == 2 and boa_choice == "home_improvement"
                else (
                    "🔥 Active Q2 5%"
                    if quarter == 2
                    else (
                        "★ Your Active 3% BoA Choice"
                        if boa_choice == "home_improvement"
                        else f"Valid Now ({today.year})"
                    )
                )
            ),
            "is_rotating_now": quarter == 2 or boa_choice == "home_improvement",
            "best_card": (
                "Discover it® (Active Q2 5% Home Improvement!) → then BoA Customized Cash (★ Active 3% Home Improvement!)"
                if quarter == 2 and boa_choice == "home_improvement"
                else (
                    "Discover it® Cash Back (Active Q2 5% Home Improvement!)"
                    if quarter == 2
                    else (
                        "Bank of America® Customized Cash Rewards (★ Active 3% Choice: Home Improvement & Furnishings!)"
                        if boa_choice == "home_improvement"
                        else "Amex Blue Cash Everyday® (3% Online at HomeDepot.com / Lowes.com / Wayfair.com)"
                    )
                )
            ),
            "best_rate": (
                "5% Cash Back (Active Q2)"
                if quarter == 2
                else (
                    "3% Cash Back In-Store & Online (Up to $2,500/qtr)"
                    if boa_choice == "home_improvement"
                    else "3% Online / 10%–15% Gift Card Bonus"
                )
            ),
            "runner_up": "Amex Blue Cash Everyday (3% Online at HomeDepot.com/Lowes.com) or Discover Gift Cards (+10%–15% Bonus)",
            "why": (
                "ACTIVE NOW: Discover it gives 5% cash back at Home Improvement Stores up to $1,500."
                if quarter == 2
                else (
                    "✓ ACTIVE NOW: Because your Bank of America Customized Cash Rewards 3% Choice Category is set to 'Home Improvement & Furnishings', you earn 3% cash back (up to $2,500/qtr) in-store AND online at Home Depot, Lowe's, IKEA, Ace Hardware, Menards, furniture stores, and contractors!"
                    if boa_choice == "home_improvement"
                    else f"Your BoA Customized Cash 3% category is currently set to '{boa_choice_label}'. Order online at HomeDepot.com/Lowes.com with Amex BCE for 3%, or switch your BoA 3% Choice Category to 'Home Improvement & Furnishings'!"
                )
            ),
            "special_activation": (
                "✓ Already selected as your active 3% Choice Category on BoA Customized Cash Rewards! If you ever change it in the BoA app, just update the 'BoA 3% Choice' selector at the top of this page."
                if boa_choice == "home_improvement"
                else "Switch your BoA 3% Choice Category to 'Home Improvement & Furnishings' in the BoA app (and in the selector at the top of this page) before big Home Depot/Lowe's/IKEA runs!"
            ),
            "portal_url": "https://www.bankofamerica.com/credit-cards/manage-your-credit-card-account/",
            "linked_perk": discover_q_perk if quarter == 2 else boa_ccr_cap,
        },
        {
            "id": "streaming_subscriptions",
            "icon": "🎬",
            "title": "Streaming Services (Disney+, Hulu, ESPN+, Apple, Netflix, Spotify)",
            "merchants": "Disney Bundle, Disney+, Hulu, ESPN+, Apple TV+, Apple Music, Netflix, Max, Spotify, YouTube TV",
            "group": "bills",
            "validity_badge": f"Valid in {cur_month_name}",
            "is_rotating_now": quarter in (1, 2),
            "best_card": "Amex Blue Cash Everyday® ($7/mo Disney) + CSR (Free Apple TV+ & Music)",
            "best_rate": "100% Free Apple + $84/yr Disney Credit",
            "runner_up": (
                "Discover it (Active Q1 5% Select Streaming!)"
                if quarter == 1
                else (
                    "Chase Freedom (Active Q2 5x Select Streaming!)"
                    if quarter == 2
                    else (
                        "BoA Customized Cash Rewards (★ Active 3% Online Shopping covers Netflix/Spotify/Max!)"
                        if boa_choice == "online_shopping"
                        else "Amex EveryDay (counts toward 20 monthly swipes for +20% MR)"
                    )
                )
            ),
            "why": "Amex BCE gives a $7/month ($84/yr) statement credit on Disney+, Hulu, or ESPN+ spend of $9.99+/mo. CSR includes complimentary Apple TV+ and Apple Music ($324/yr).",
            "special_activation": "⚠️ Enroll in the $7/mo Disney Bundle credit inside Amex Benefits, and activate Apple TV+ & Apple Music inside Chase Benefits.",
            "portal_url": "https://global.americanexpress.com/card-benefits",
            "linked_perk": bce_disney,
        },
        {
            "id": "museums_culture",
            "icon": "🏛️",
            "title": "Museums, Science Centers, Botanical Gardens & Zoos",
            "merchants": "227 Partner Museums Nationwide (de Young, Legion of Honor, LACMA, Met, Art Institute)",
            "group": "travel",
            "validity_badge": "1st Full Weekend Monthly",
            "is_rotating_now": False,
            "best_card": "Bank of America® Customized Cash Rewards & Bank of America® Travel Rewards",
            "best_rate": "FREE Admission (2 Cards = 2 Tickets!)",
            "runner_up": "Discover it (5% Q4 Entertainment)" if quarter == 4 else "CSR ($300 Travel Credit)",
            "why": "Because you hold BOTH Bank of America Customized Cash Rewards and BoA Travel Rewards, you have two physical Bank of America cards for free general admission during the first full weekend (Sat & Sun) of every month at 227 museums!",
            "special_activation": "No online activation needed — simply show your physical BoA Customized Cash Rewards and/or BoA Travel Rewards card and photo ID at the museum box office on the 1st full weekend of the month.",
            "portal_url": "https://about.bankofamerica.com/en/making-an-impact/museums-on-us-partners",
            "linked_perk": boa_museums,
        },
    ]

    return items


# ---------------------------------------------------------------------------
# Section 2: Upcoming Next Month & Next Quarter Resets & Rotations
# ---------------------------------------------------------------------------
def build_upcoming_rotations(
    today: dt.date,
    next_museums_weekend: str = "November 7th and 8th",
) -> list[dict[str, Any]]:
    quarter = (today.month - 1) // 3 + 1
    next_q = 1 if quarter == 4 else quarter + 1
    next_q_year = today.year + 1 if quarter == 4 else today.year

    # Compute next month date & days until next month
    if today.month == 12:
        next_month_dt = dt.date(today.year + 1, 1, 1)
    else:
        next_month_dt = dt.date(today.year, today.month + 1, 1)
    days_to_next_month = (next_month_dt - today).days
    next_month_name = FULL_MONTH_NAMES[next_month_dt.month - 1]

    # Compute next quarter start date & days until next quarter
    next_q_start_month = (next_q - 1) * 3 + 1
    next_q_dt = dt.date(next_q_year, next_q_start_month, 1)
    days_to_next_q = (next_q_dt - today).days

    discover_rotations = {
        1: {
            "window": f"Q1 {next_q_year} (Jan 1 – Mar 31)",
            "categories": "Discover it® (Groceries, Wholesale Clubs & Streaming) + Chase Freedom® (Groceries, Fitness Clubs & Cruises)",
            "details": "In Q1, BOTH your Chase Freedom (5x Ultimate Rewards up to $1,500) and Discover it (5% cash back up to $1,500) rotate to Grocery Stores — giving you $3,000 in combined 5% supermarket capacity!",
        },
        2: {
            "window": f"Q2 {next_q_year} (Apr 1 – Jun 30)",
            "categories": "Chase Freedom® (Amazon.com, Whole Foods & Streaming) + Discover it® (Restaurants & Home Improvement)",
            "details": "In Q2, Chase Freedom earns 5x UR at Amazon.com, Whole Foods & Select Streaming (up to $1,500), while Discover it earns 5% at Restaurants & Home Improvement Stores (up to $1,500).",
        },
        3: {
            "window": f"Q3 {next_q_year} (Jul 1 – Sep 30)",
            "categories": "Chase Freedom® (Gas/EV, Live Entertainment & Movies) + Discover it® (Gas/EV, Public Transit & Drug Stores)",
            "details": "In Q3, BOTH Chase Freedom (5x UR) and Discover it (5%) cover Gas Stations & EV Charging, plus Live Entertainment/Movies on Freedom and Public Transit/Drugstores on Discover.",
        },
        4: {
            "window": f"Q4 {next_q_year} (Oct 1 – Dec 31)",
            "categories": "Chase Freedom® (PayPal, McDonald's, Pet Shops/Vet & Charities) + Discover it® (Restaurants, Entertainment & Utilities)",
            "details": "In Q4, Chase Freedom earns 5x UR on PayPal, McDonald's & Pet Shops/Vets, while Discover it earns 5% on Restaurants, Entertainment & Utilities.",
        },
    }
    next_disc = discover_rotations[next_q]

    upcoming: list[dict[str, Any]] = [
        {
            "cadence_badge": f"Next Month • Starts {next_month_name} 1 (in {days_to_next_month}d)",
            "icon": "🔄",
            "title": f"{next_month_name} 1st Monthly Statement Credits & BoA 3% Category Switch Window",
            "cards": "Chase Sapphire Reserve • Amex BCE • Amex EveryDay • BoA Customized Cash Rewards",
            "highlight": "$35 CSR DoorDash ($15 + $10 + $10) • $10 CSR Lyft • $10 CSR Peloton • $7 BCE Disney+ • $15 BCE Home Chef • BoA 3% Choice Switch",
            "description": f"All monthly credits reset on {next_month_name} 1, and your new monthly window opens to change your 3% Choice Category on Bank of America Customized Cash Rewards (Online Shopping, Gas, Dining, Travel, Drug Stores, or Home Improvement).",
            "action_note": "Also resets your Amex EveryDay 20-Swipe counter (0/20) for +20% bonus MR points.",
        },
        {
            "cadence_badge": f"Next Month Weekend • {next_museums_weekend}",
            "icon": "🏛️",
            "title": f"Bank of America Museums on Us® Weekend ({next_museums_weekend})",
            "cards": "Bank of America® Customized Cash Rewards & Bank of America® Travel Rewards",
            "highlight": "FREE Admission at 227 Partner Museums, Science Centers, Gardens & Zoos",
            "description": f"Scraped live from Bank of America: the next eligible Museums on Us weekend is {next_museums_weekend}. Present both your BoA Customized Cash Rewards and BoA Travel Rewards cards with photo ID.",
            "action_note": "No prior registration required — just bring your physical BoA credit cards.",
        },
        {
            "cadence_badge": f"Cardmember Anniversary • {next_month_name} 1 (in {days_to_next_month}d)",
            "icon": "✈️",
            "title": "Chase Sapphire Reserve $300 Annual Travel Credit Renewal",
            "cards": "Chase Sapphire Reserve®",
            "highlight": "$300 Automatic Travel Credit Resets on Anniversary Date",
            "description": "Your CSR $300 Annual Travel Credit resets on your cardmember anniversary date (configured as Nov 1). Any airlines, hotels, transit, parking, or tolls charged after renewal automatically trigger the fresh $300 credit.",
            "action_note": "Automatic — no portal activation needed.",
        },
        {
            "cadence_badge": f"Next Quarter • {next_disc['window']} (in {days_to_next_q}d)",
            "icon": "🔥",
            "title": f"Upcoming 5% Quarterly Rotations: {next_disc['categories']}",
            "cards": "Chase Freedom® (5% / 5x UR) & Discover it® Cash Back (5%)",
            "highlight": f"2 × $1,500 = $3,000 Combined 5% Spend Capacity ($150 / 7,500 UR + $75 Cash) in {next_disc['window']}",
            "description": next_disc["details"],
            "action_note": "⚠️ Requires quarterly activation at ChaseBonus.com (Chase Freedom) and Discover.com (Discover it)!",
        },
        {
            "cadence_badge": f"Next Quarter & New Year • Jan 1, {today.year + 1} (in {days_to_next_q}d)",
            "icon": "💎",
            "title": f"H1 (Jan–Jun {today.year + 1}) Biannual Credits, BoA $2,500/qtr Cap & Amex $6,000/yr Caps Reset",
            "cards": "CSR • Chase Freedom • BoA Customized Cash Rewards • Amex BCE • Amex EveryDay",
            "highlight": "$150 CSR Exclusive Tables H1 • $150 CSR StubHub H1 • $500 CSR The Edit • $2,500 BoA Q1 3%/2% Cap • $10 Freedom DoorDash Q1",
            "description": f"On January 1, {today.year + 1}, all semi-annual H1 credits reset ($150 OpenTable Dining + $150 StubHub), CSR's $500 The Edit resets, Bank of America Customized Cash Rewards' $2,500 quarterly 3%/2% cap resets, and all $6,000/yr caps reset on Amex BCE and Amex EveryDay.",
            "action_note": "Use your current H2 ($150 Dining & $150 StubHub) credits and Q4 caps before Dec 31!",
        },
    ]

    return upcoming


# ---------------------------------------------------------------------------
# Period Definition Helpers
# ---------------------------------------------------------------------------
def get_periods_for_frequency(
    frequency: str, year: int, anniversary_date_str: str = "2026-11-01"
) -> list[dict[str, Any]]:
    if frequency == "monthly":
        periods = []
        for m in range(1, 13):
            last_day = calendar.monthrange(year, m)[1]
            periods.append(
                {
                    "key": f"M{m:02d}",
                    "label": MONTH_NAMES[m - 1],
                    "full_label": f"{MONTH_NAMES[m - 1]} {year}",
                    "start_date": f"{year}-{m:02d}-01",
                    "end_date": f"{year}-{m:02d}-{last_day:02d}",
                }
            )
        return periods

    if frequency == "quarterly":
        quarters = [
            ("Q1", "Q1 (Jan–Mar)", f"{year}-01-01", f"{year}-03-31"),
            ("Q2", "Q2 (Apr–Jun)", f"{year}-04-01", f"{year}-06-30"),
            ("Q3", "Q3 (Jul–Sep)", f"{year}-07-01", f"{year}-09-30"),
            ("Q4", "Q4 (Oct–Dec)", f"{year}-10-01", f"{year}-12-31"),
        ]
        return [
            {"key": k, "label": lbl, "full_label": f"{lbl} {year}", "start_date": s, "end_date": e}
            for k, lbl, s, e in quarters
        ]

    if frequency == "q1_only":
        return [
            {
                "key": "Q1",
                "label": "Q1 (Jan–Mar) — $1,500 Cap",
                "full_label": f"Q1 {year} (Jan 1 – Mar 31)",
                "start_date": f"{year}-01-01",
                "end_date": f"{year}-03-31",
            }
        ]
    if frequency == "q2_only":
        return [
            {
                "key": "Q2",
                "label": "Q2 (Apr–Jun) — $1,500 Cap",
                "full_label": f"Q2 {year} (Apr 1 – Jun 30)",
                "start_date": f"{year}-04-01",
                "end_date": f"{year}-06-30",
            }
        ]
    if frequency == "q3_only":
        return [
            {
                "key": "Q3",
                "label": "Q3 (Jul–Sep) — $1,500 Cap",
                "full_label": f"Q3 {year} (Jul 1 – Sep 30)",
                "start_date": f"{year}-07-01",
                "end_date": f"{year}-09-30",
            }
        ]
    if frequency == "q4_only":
        return [
            {
                "key": "Q4",
                "label": "Q4 (Oct–Dec) — $1,500 Cap",
                "full_label": f"Q4 {year} (Oct 1 – Dec 31)",
                "start_date": f"{year}-10-01",
                "end_date": f"{year}-12-31",
            }
        ]

    if frequency == "semi_annual":
        return [
            {
                "key": "H1",
                "label": "H1 (Jan–Jun)",
                "full_label": f"Jan–Jun {year}",
                "start_date": f"{year}-01-01",
                "end_date": f"{year}-06-30",
            },
            {
                "key": "H2",
                "label": "H2 (Jul–Dec)",
                "full_label": f"Jul–Dec {year}",
                "start_date": f"{year}-07-01",
                "end_date": f"{year}-12-31",
            },
        ]

    if frequency == "semi_annual_bookings":
        return [
            {
                "key": "STAY1",
                "label": "Stay #1 ($250)",
                "full_label": f"The Edit Booking #1 ({year})",
                "start_date": f"{year}-01-01",
                "end_date": f"{year}-12-31",
            },
            {
                "key": "STAY2",
                "label": "Stay #2 ($250)",
                "full_label": f"The Edit Booking #2 ({year})",
                "start_date": f"{year}-01-01",
                "end_date": f"{year}-12-31",
            },
        ]

    if frequency == "anniversary":
        try:
            anniv = dt.date.fromisoformat(anniversary_date_str)
            month, day = anniv.month, anniv.day
        except ValueError:
            month, day = 11, 1
        try:
            end_dt = dt.date(year, month, day) - dt.timedelta(days=1)
        except ValueError:
            end_dt = dt.date(year, month, 28) - dt.timedelta(days=1)
        today = dt.date.today()
        if end_dt < dt.date(year, 1, 1) or (year == today.year and end_dt < today):
            try:
                start_dt = dt.date(year, month, day)
                end_dt = dt.date(year + 1, month, day) - dt.timedelta(days=1)
            except ValueError:
                start_dt = dt.date(year, month, 28)
                end_dt = dt.date(year + 1, month, 28) - dt.timedelta(days=1)
        else:
            try:
                start_dt = dt.date(year - 1, month, day)
            except ValueError:
                start_dt = dt.date(year - 1, month, 28)

        return [
            {
                "key": "ANNIV",
                "label": f"Cardmember Yr (renews {MONTH_NAMES[month - 1]} {day})",
                "full_label": f"Anniversary Cycle ({start_dt.isoformat()} to {end_dt.isoformat()})",
                "start_date": start_dt.isoformat(),
                "end_date": end_dt.isoformat(),
            }
        ]

    if frequency == "quadrennial":
        return [
            {
                "key": "QUAD",
                "label": "4-Year Cycle ($120)",
                "full_label": "4-Year Global Entry / PreCheck Cycle",
                "start_date": f"{year}-01-01",
                "end_date": f"{year + 3}-12-31",
            }
        ]

    return [
        {
            "key": "ANNUAL",
            "label": f"Full Year {year}",
            "full_label": f"Calendar Year {year}",
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
        }
    ]


# ---------------------------------------------------------------------------
# Database Layer
# ---------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                preset_key TEXT DEFAULT 'custom',
                issuer TEXT DEFAULT 'Chase',
                annual_fee REAL NOT NULL DEFAULT 795.0,
                au_fee REAL NOT NULL DEFAULT 195.0,
                au_count INTEGER NOT NULL DEFAULT 0,
                anniversary_date TEXT NOT NULL DEFAULT '2026-11-01',
                color_theme TEXT NOT NULL DEFAULT 'sapphire',
                earning_summary TEXT DEFAULT '',
                webhook_url TEXT DEFAULT '',
                include_lifestyle_in_roi INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS perks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Travel',
                credit_type TEXT NOT NULL DEFAULT 'hard_credit',
                frequency TEXT NOT NULL DEFAULT 'monthly',
                period_amount REAL NOT NULL DEFAULT 10.0,
                requires_activation INTEGER NOT NULL DEFAULT 0,
                activation_done INTEGER NOT NULL DEFAULT 0,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                caveats TEXT DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 99
            );

            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                perk_id INTEGER NOT NULL REFERENCES perks(id) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                period_key TEXT NOT NULL,
                used_amount REAL NOT NULL DEFAULT 0.0,
                used_date TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                UNIQUE(perk_id, year, period_key)
            );

            CREATE TABLE IF NOT EXISTS issuer_sync_status (
                preset_key TEXT PRIMARY KEY,
                card_name TEXT NOT NULL,
                issuer TEXT NOT NULL,
                url TEXT NOT NULL,
                activation_url TEXT NOT NULL,
                http_status INTEGER NOT NULL DEFAULT 200,
                online INTEGER NOT NULL DEFAULT 1,
                page_title TEXT DEFAULT '',
                verified_rules TEXT DEFAULT '',
                live_note TEXT DEFAULT '',
                last_checked TEXT DEFAULT ''
            );
            """
        )
        vendor_engine.ensure_vendor_tables(conn)

        cols = [r["name"] for r in conn.execute("PRAGMA table_info(cards)").fetchall()]
        if "earning_summary" not in cols:
            conn.execute("ALTER TABLE cards ADD COLUMN earning_summary TEXT DEFAULT ''")

        existing_presets = {
            r["preset_key"]: r["id"]
            for r in conn.execute("SELECT id, preset_key FROM cards").fetchall()
        }

        # Automatically migrate old cfu -> chase_freedom and bankamericard -> boa_ccr
        migrations = [("cfu", "chase_freedom"), ("bankamericard", "boa_ccr")]
        migrated_any = False
        for old_key, new_key in migrations:
            if old_key in existing_presets:
                cid = existing_presets.pop(old_key)
                new_preset = PRESET_CARDS[new_key]
                conn.execute(
                    """
                    UPDATE cards
                    SET name = ?, preset_key = ?, issuer = ?, earning_summary = ?
                    WHERE id = ?
                    """,
                    (
                        new_preset["name"],
                        new_key,
                        new_preset["issuer"],
                        new_preset["earning_summary"],
                        cid,
                    ),
                )
                conn.execute("DELETE FROM perks WHERE card_id = ?", (cid,))
                insert_preset_perks(conn, cid, new_preset["perks"])
                conn.execute("DELETE FROM issuer_sync_status WHERE preset_key = ?", (old_key,))
                existing_presets[new_key] = cid
                migrated_any = True

        if migrated_any:
            conn.execute("DELETE FROM sync_metadata WHERE key = 'last_sync_date'")

        has_any_active = (
            conn.execute("SELECT COUNT(*) FROM cards WHERE is_active = 1").fetchone()[0] > 0
        )

        for idx, preset_key in enumerate(USER_WALLET_PRESETS):
            preset = PRESET_CARDS[preset_key]
            if preset_key not in existing_presets:
                is_act = 1 if (idx == 0 and not has_any_active) else 0
                seed_card_from_preset(conn, preset_key, is_active=is_act)
                if is_act:
                    has_any_active = True
            else:
                conn.execute(
                    "UPDATE cards SET earning_summary = ? WHERE id = ? AND (earning_summary IS NULL OR earning_summary = '')",
                    (preset.get("earning_summary", ""), existing_presets[preset_key]),
                )
        row_choice = conn.execute(
            "SELECT value FROM sync_metadata WHERE key = 'boa_ccr_choice_category'"
        ).fetchone()
        current_choice = row_choice["value"] if row_choice else "home_improvement"
        set_boa_choice_category(conn, current_choice)
        conn.commit()

    maybe_sync_on_first_open_of_day()


def seed_card_from_preset(conn: sqlite3.Connection, preset_key: str, is_active: int = 1) -> int:
    preset = PRESET_CARDS[preset_key]
    cur = conn.execute(
        """
        INSERT INTO cards (
            name, preset_key, issuer, annual_fee, au_fee, au_count,
            anniversary_date, color_theme, earning_summary, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            preset["name"],
            preset["preset_key"],
            preset["issuer"],
            preset["annual_fee"],
            preset["au_fee"],
            preset["au_count"],
            preset["anniversary_date"],
            preset["color_theme"],
            preset.get("earning_summary", ""),
            is_active,
        ),
    )
    card_id = int(cur.lastrowid)
    insert_preset_perks(conn, card_id, preset["perks"])
    return card_id


def insert_preset_perks(conn: sqlite3.Connection, card_id: int, perks: list[dict[str, Any]]) -> None:
    for p in perks:
        conn.execute(
            """
            INSERT INTO perks (
                card_id, name, category, credit_type, frequency,
                period_amount, requires_activation, activation_done, caveats, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                card_id,
                p["name"],
                p["category"],
                p["credit_type"],
                p["frequency"],
                p["period_amount"],
                p["requires_activation"],
                p["activation_done"],
                p["caveats"],
                p["sort_order"],
            ),
        )


# ---------------------------------------------------------------------------
# State Computation & Analytics
# ---------------------------------------------------------------------------
def compute_card_metrics(
    card: dict[str, Any],
    perks_rows: list[dict[str, Any]],
    usage_map: dict[tuple[int, str], dict[str, Any]],
    year: int,
    today: dt.date,
) -> dict[str, Any]:
    enriched_perks: list[dict[str, Any]] = []
    expiring_alerts: list[dict[str, Any]] = []

    total_hard_max = 0.0
    total_hard_used = 0.0
    total_lifestyle_max = 0.0
    total_lifestyle_used = 0.0
    category_breakdown: dict[str, dict[str, float]] = {}

    for perk in perks_rows:
        periods_meta = get_periods_for_frequency(
            perk["frequency"], year, card["anniversary_date"]
        )
        period_amount = float(perk["period_amount"])
        periods_out = []
        perk_used_total = 0.0
        perk_max_total = period_amount * len(periods_meta)
        all_periods_expired = True

        for pm in periods_meta:
            u_entry = usage_map.get((perk["id"], pm["key"]))
            used_amt = float(u_entry["used_amount"]) if u_entry else 0.0
            used_amt = max(0.0, min(used_amt, period_amount))
            perk_used_total += used_amt

            start_dt = dt.date.fromisoformat(pm["start_date"])
            end_dt = dt.date.fromisoformat(pm["end_date"])
            is_current_period = start_dt <= today <= end_dt
            is_expired_period = end_dt < today
            if not is_expired_period:
                all_periods_expired = False
            days_left = (end_dt - today).days

            status = "unused"
            if used_amt >= period_amount - 0.009:
                status = "completed"
            elif used_amt > 0:
                status = "partial"

            period_obj = {
                **pm,
                "max_amount": period_amount,
                "used_amount": round(used_amt, 2),
                "remaining_amount": round(max(0.0, period_amount - used_amt), 2),
                "status": status,
                "is_current": is_current_period,
                "is_expired": is_expired_period,
                "days_left": days_left,
                "used_date": u_entry["used_date"] if u_entry else "",
                "notes": u_entry["notes"] if u_entry else "",
            }
            periods_out.append(period_obj)

            remaining = period_amount - used_amt
            if (
                perk["is_enabled"]
                and remaining > 0.01
                and is_current_period
                and 0 <= days_left <= 35
            ):
                urgency = "critical" if days_left <= 7 else ("warning" if days_left <= 15 else "info")
                expiring_alerts.append(
                    {
                        "card_id": card["id"],
                        "card_name": card["name"],
                        "perk_id": perk["id"],
                        "perk_name": perk["name"],
                        "category": perk["category"],
                        "period_key": pm["key"],
                        "period_label": pm["full_label"],
                        "remaining_amount": round(remaining, 2),
                        "max_amount": period_amount,
                        "end_date": pm["end_date"],
                        "days_left": days_left,
                        "urgency": urgency,
                        "requires_activation": bool(
                            perk["requires_activation"] and not perk["activation_done"]
                        ),
                    }
                )

        is_swipe_counter = (
            perk["category"] == "Multiplier Bonus" and "20+" in perk["name"]
        )
        if perk["is_enabled"] and not is_swipe_counter:
            if perk["credit_type"] == "hard_credit":
                total_hard_max += perk_max_total
                total_hard_used += perk_used_total
            else:
                total_lifestyle_max += perk_max_total
                total_lifestyle_used += perk_used_total

            cat = perk["category"]
            if cat not in category_breakdown:
                category_breakdown[cat] = {"used": 0.0, "max": 0.0}
            category_breakdown[cat]["used"] = round(
                category_breakdown[cat]["used"] + perk_used_total, 2
            )
            category_breakdown[cat]["max"] = round(
                category_breakdown[cat]["max"] + perk_max_total, 2
            )

        enriched_perks.append(
            {
                **perk,
                "is_expired_perk": all_periods_expired,
                "annual_max": round(perk_max_total, 2),
                "annual_used": round(perk_used_total, 2),
                "annual_remaining": round(max(0.0, perk_max_total - perk_used_total), 2),
                "utilization_pct": round((perk_used_total / perk_max_total * 100.0), 1)
                if perk_max_total > 0
                else 0.0,
                "periods": periods_out,
            }
        )

    effective_annual_fee = float(card["annual_fee"]) + (
        float(card["au_fee"]) * int(card["au_count"])
    )
    include_lifestyle = bool(card["include_lifestyle_in_roi"])
    counted_used = total_hard_used + (total_lifestyle_used if include_lifestyle else 0.0)
    counted_max = total_hard_max + (total_lifestyle_max if include_lifestyle else 0.0)
    net_value = counted_used - effective_annual_fee
    breakeven_pct = (
        round((counted_used / effective_annual_fee) * 100.0, 1)
        if effective_annual_fee > 0
        else 100.0
    )

    summary = {
        "year": year,
        "today": today.isoformat(),
        "effective_annual_fee": round(effective_annual_fee, 2),
        "include_lifestyle_in_roi": include_lifestyle,
        "hard_credits_used": round(total_hard_used, 2),
        "hard_credits_max": round(total_hard_max, 2),
        "lifestyle_used": round(total_lifestyle_used, 2),
        "lifestyle_max": round(total_lifestyle_max, 2),
        "all_perks_used": round(total_hard_used + total_lifestyle_used, 2),
        "all_perks_max": round(total_hard_max + total_lifestyle_max, 2),
        "counted_used": round(counted_used, 2),
        "counted_max": round(counted_max, 2),
        "net_value": round(net_value, 2),
        "amount_to_breakeven": round(max(0.0, effective_annual_fee - counted_used), 2),
        "breakeven_pct": breakeven_pct,
        "total_utilization_pct": round((counted_used / counted_max * 100.0), 1)
        if counted_max > 0
        else 0.0,
        "unactivated_count": sum(
            1
            for p in enriched_perks
            if p["is_enabled"]
            and not p["is_expired_perk"]
            and p["requires_activation"]
            and not p["activation_done"]
        ),
        "category_breakdown": category_breakdown,
    }

    return {
        "summary": summary,
        "perks": enriched_perks,
        "expiring_alerts": expiring_alerts,
    }


def build_dashboard_state(year: int | None = None, card_id: int | None = None) -> dict[str, Any]:
    today = dt.date.today()
    if year is None:
        year = today.year

    with get_db() as conn:
        cards_rows = [dict(r) for r in conn.execute("SELECT * FROM cards ORDER BY id ASC").fetchall()]
        if not cards_rows:
            init_db()
            cards_rows = [dict(r) for r in conn.execute("SELECT * FROM cards ORDER BY id ASC").fetchall()]

        all_perks_rows = [
            dict(r)
            for r in conn.execute("SELECT * FROM perks ORDER BY sort_order ASC, id ASC").fetchall()
        ]
        all_usage_rows = conn.execute(
            """
            SELECT u.* FROM usage_logs u
            JOIN perks p ON p.id = u.perk_id
            WHERE u.year = ? OR p.frequency = 'quadrennial'
            """,
            (year,),
        ).fetchall()
        issuer_sync_rows = [
            dict(r) for r in conn.execute("SELECT * FROM issuer_sync_status").fetchall()
        ]
        vendor_engine.ensure_vendor_tables(conn)
        scraped_vendors_count = conn.execute("SELECT COUNT(*) FROM scraped_vendors").fetchone()[0]
        meta_rows = {
            r["key"]: r["value"]
            for r in conn.execute("SELECT key, value FROM sync_metadata").fetchall()
        }

    usage_map: dict[tuple[int, str], dict[str, Any]] = {}
    for u in all_usage_rows:
        u_dict = dict(u)
        usage_map[(u_dict["perk_id"], u_dict["period_key"])] = u_dict

    perks_by_card: dict[int, list[dict[str, Any]]] = {}
    for p in all_perks_rows:
        perks_by_card.setdefault(p["card_id"], []).append(p)

    active_card = None
    if card_id is not None:
        for c in cards_rows:
            if c["id"] == card_id:
                active_card = c
                break
    if active_card is None:
        active_card = next((c for c in cards_rows if c["is_active"] == 1), cards_rows[0])

    wallet_cards_summary: list[dict[str, Any]] = []
    wallet_total_fee = 0.0
    wallet_total_used = 0.0
    wallet_total_max = 0.0
    wallet_all_alerts: list[dict[str, Any]] = []
    enriched_perks_by_preset: dict[str, list[dict[str, Any]]] = {}

    active_computed = None

    for c in cards_rows:
        c_perks = perks_by_card.get(c["id"], [])
        comp = compute_card_metrics(c, c_perks, usage_map, year, today)
        enriched_perks_by_preset[c["preset_key"]] = comp["perks"]
        if c["id"] == active_card["id"]:
            active_computed = comp

        is_legacy_duplicate = c["preset_key"] == "csr_550" and any(
            x["preset_key"] == "csr_795" for x in cards_rows
        )

        s = comp["summary"]
        wallet_cards_summary.append(
            {
                "id": c["id"],
                "name": c["name"],
                "issuer": c["issuer"],
                "preset_key": c["preset_key"],
                "annual_fee": s["effective_annual_fee"],
                "counted_used": s["counted_used"],
                "counted_max": s["counted_max"],
                "all_perks_max": s["all_perks_max"],
                "net_value": s["net_value"],
                "utilization_pct": s["total_utilization_pct"],
                "unactivated_count": s["unactivated_count"],
                "expiring_count": len(comp["expiring_alerts"]),
                "earning_summary": c.get("earning_summary", ""),
                "is_legacy_duplicate": is_legacy_duplicate,
            }
        )
        if not is_legacy_duplicate:
            wallet_total_fee += s["effective_annual_fee"]
            wallet_total_used += s["counted_used"]
            wallet_total_max += s["counted_max"]
            wallet_all_alerts.extend(comp["expiring_alerts"])

    assert active_computed is not None
    active_alerts = sorted(
        active_computed["expiring_alerts"], key=lambda x: (x["days_left"], -x["remaining_amount"])
    )
    wallet_all_alerts.sort(key=lambda x: (x["days_left"], -x["remaining_amount"]))

    quarter = (today.month - 1) // 3 + 1
    next_museums_weekend = meta_rows.get("next_museums_weekend", "November 7th and 8th")
    boa_choice = meta_rows.get("boa_ccr_choice_category", "home_improvement")

    return {
        "cards": cards_rows,
        "active_card": active_card,
        "summary": active_computed["summary"],
        "perks": active_computed["perks"],
        "expiring_alerts": active_alerts,
        "boa_choice_category": boa_choice,
        "boa_choice_options": BOA_CCR_CHOICE_OPTIONS,
        "wallet_overview": {
            "current_quarter": quarter,
            "total_annual_fee": round(wallet_total_fee, 2),
            "total_used": round(wallet_total_used, 2),
            "total_max": round(wallet_total_max, 2),
            "net_value": round(wallet_total_used - wallet_total_fee, 2),
            "cards": wallet_cards_summary,
            "all_expiring_alerts": wallet_all_alerts[:15],
        },
        "merchant_advisor": build_merchant_advisor(today, enriched_perks_by_preset, boa_choice=boa_choice),
        "upcoming_rotations": build_upcoming_rotations(today, next_museums_weekend),
        "issuer_sync_status": issuer_sync_rows,
        "scraped_vendors_count": scraped_vendors_count,
        "sync_schedule": {
            "mode": "first_open_of_day",
            "last_sync_date": meta_rows.get("last_sync_date", today.isoformat()),
            "last_sync_utc": meta_rows.get("last_sync_utc", ""),
            "last_trigger": meta_rows.get("last_sync_trigger", "first_open_of_day"),
        },
    }


# ---------------------------------------------------------------------------
# iCalendar (.ics) Export Generator
# ---------------------------------------------------------------------------
def generate_ics_calendar(year: int, card_id: int | None = None) -> str:
    state = build_dashboard_state(year=year, card_id=card_id)
    card = state["active_card"]
    now_utc = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Credit Card Tracker//Self-Hosted//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{card['name']} Perk Reminders ({year})",
    ]

    for perk in state["perks"]:
        if not perk["is_enabled"]:
            continue
        for p in perk["periods"]:
            if p["status"] == "completed" or p.get("is_expired"):
                continue
            end_dt = dt.date.fromisoformat(p["end_date"])
            rem_dt = end_dt - dt.timedelta(days=3)
            dtstart = rem_dt.strftime("%Y%m%d")
            dtend = (rem_dt + dt.timedelta(days=1)).strftime("%Y%m%d")
            uid = f"cc-tracker-{card['id']}-{perk['id']}-{year}-{p['key']}@selfhosted.local"
            remaining = p["remaining_amount"]
            summary_txt = f"[{card['name']}] Use ${remaining:.0f} {perk['name']} ({p['label']})"
            desc_txt = (
                f"Card: {card['name']}\\n"
                f"Perk: {perk['name']} ({p['full_label']})\\n"
                f"Remaining Value: ${remaining:.2f} of ${p['max_amount']:.2f}\\n"
                f"Expires: {p['end_date']}\\n"
                f"Details: {perk['caveats']}"
            )
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTAMP:{now_utc}",
                    f"DTSTART;VALUE=DATE:{dtstart}",
                    f"DTEND;VALUE=DATE:{dtend}",
                    f"SUMMARY:{summary_txt}",
                    f"DESCRIPTION:{desc_txt}",
                    "BEGIN:VALARM",
                    "TRIGGER:-PT9H",
                    "ACTION:DISPLAY",
                    f"DESCRIPTION:{summary_txt}",
                    "END:VALARM",
                    "END:VEVENT",
                ]
            )

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------
# Webhook Digest Notification Sender
# ---------------------------------------------------------------------------
def build_webhook_digest(year: int, card_id: int | None = None) -> dict[str, Any]:
    state = build_dashboard_state(year=year, card_id=card_id)
    card = state["active_card"]
    summary = state["summary"]
    alerts = state["expiring_alerts"]

    lines = [
        f"💳 **{card['name']} — Credit Card Tracker Digest ({year})**",
        f"• **Realized Value:** ${summary['counted_used']:.2f} / ${summary['counted_max']:.2f} "
        f"(Annual Fee: ${summary['effective_annual_fee']:.2f} | Net: "
        f"{'+' if summary['net_value'] >= 0 else ''}${summary['net_value']:.2f})",
    ]
    if alerts:
        lines.append(f"\n⏰ **Expiring Soon ({len(alerts)} active credits):**")
        for a in alerts[:8]:
            lines.append(
                f"  - **{a['perk_name']}** ({a['period_label']}): "
                f"**${a['remaining_amount']:.2f}** left — expires in **{a['days_left']}d** ({a['end_date']})"
            )
    else:
        lines.append("\n✅ All current-period credits are completed! Nothing expiring soon.")

    message_text = "\n".join(lines)
    return {
        "card_name": card["name"],
        "webhook_url": card.get("webhook_url", ""),
        "message": message_text,
        "summary": summary,
        "alerts_count": len(alerts),
    }


def dispatch_webhook(webhook_url: str, message_text: str) -> dict[str, Any]:
    if not webhook_url:
        return {"sent": False, "reason": "No webhook URL configured (preview generated)."}

    payload = json.dumps(
        {
            "content": message_text,
            "text": message_text,
            "title": "Credit Card Tracker Alert",
            "message": message_text,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "CreditCardTracker/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return {"sent": True, "status_code": resp.status}
    except Exception as exc:
        return {"sent": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# HTTP Request Handler
# ---------------------------------------------------------------------------
class TrackerHTTPRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _serve_static(self, rel_path: str) -> None:
        if rel_path in ("", "/"):
            rel_path = "index.html"
        rel_path = rel_path.lstrip("/")
        target = (STATIC_DIR / rel_path).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return

        mime_map = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json; charset=utf-8",
            ".ico": "image/x-icon",
        }
        ctype = mime_map.get(target.suffix.lower(), "application/octet-stream")
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/state":
            # Sync once per calendar day on first website open; skip if already synced today
            did_sync_now = maybe_sync_on_first_open_of_day()
            year = int(qs.get("year", [dt.date.today().year])[0])
            card_id = int(qs["card_id"][0]) if "card_id" in qs and qs["card_id"][0] else None
            state = build_dashboard_state(year=year, card_id=card_id)
            state["sync_schedule"]["did_sync_on_this_open"] = did_sync_now
            self._send_json(state)
            return

        if path == "/api/vendor-lookup":
            q = qs.get("q", [""])[0]
            with get_db() as conn:
                result = vendor_engine.lookup_any_vendor(conn, q, dt.date.today())
            self._send_json(result)
            return

        if path == "/api/calendar.ics":
            year = int(qs.get("year", [dt.date.today().year])[0])
            card_id = int(qs["card_id"][0]) if "card_id" in qs and qs["card_id"][0] else None
            ics_content = generate_ics_calendar(year=year, card_id=card_id)
            body = ics_content.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/calendar; charset=utf-8")
            self.send_header(
                "Content-Disposition", f'attachment; filename="credit-card-tracker-{year}.ics"'
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/export":
            with get_db() as conn:
                export_payload = {
                    "version": 1,
                    "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "cards": [dict(r) for r in conn.execute("SELECT * FROM cards").fetchall()],
                    "perks": [dict(r) for r in conn.execute("SELECT * FROM perks").fetchall()],
                    "usage_logs": [
                        dict(r) for r in conn.execute("SELECT * FROM usage_logs").fetchall()
                    ],
                }
            self._send_json(export_payload)
            return

        self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        if path == "/api/sync-issuers":
            year = int(body.get("year", dt.date.today().year))
            card_id = int(body["card_id"]) if body.get("card_id") else None
            sync_all_issuer_pages(trigger_source="manual_button")
            self._send_json(build_dashboard_state(year=year, card_id=card_id))
            return

        if path == "/api/boa-choice-category":
            choice_key = str(body.get("choice_category", "home_improvement"))
            year = int(body.get("year", dt.date.today().year))
            card_id = int(body["card_id"]) if body.get("card_id") else None
            with get_db() as conn:
                set_boa_choice_category(conn, choice_key)
            self._send_json(build_dashboard_state(year=year, card_id=card_id))
            return

        if path == "/api/usage":
            perk_id = int(body["perk_id"])
            year = int(body.get("year", dt.date.today().year))
            period_key = str(body["period_key"])
            mode = body.get("mode", "toggle")

            with get_db() as conn:
                perk = conn.execute("SELECT * FROM perks WHERE id = ?", (perk_id,)).fetchone()
                if not perk:
                    self._send_json({"error": "Perk not found"}, HTTPStatus.NOT_FOUND)
                    return

                max_amt = float(perk["period_amount"])
                existing = conn.execute(
                    "SELECT * FROM usage_logs WHERE perk_id = ? AND year = ? AND period_key = ?",
                    (perk_id, year, period_key),
                ).fetchone()

                if mode == "toggle":
                    current_amt = float(existing["used_amount"]) if existing else 0.0
                    new_amt = 0.0 if current_amt >= max_amt - 0.009 else max_amt
                    notes = existing["notes"] if existing else ""
                else:
                    new_amt = max(0.0, min(float(body.get("used_amount", 0.0)), max_amt))
                    notes = str(body.get("notes", existing["notes"] if existing else ""))

                used_date = (
                    str(body.get("used_date") or dt.date.today().isoformat())
                    if new_amt > 0
                    else ""
                )

                conn.execute(
                    """
                    INSERT INTO usage_logs (perk_id, year, period_key, used_amount, used_date, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(perk_id, year, period_key) DO UPDATE SET
                        used_amount = excluded.used_amount,
                        used_date = excluded.used_date,
                        notes = excluded.notes
                    """,
                    (perk_id, year, period_key, new_amt, used_date, notes),
                )
                conn.commit()

            state = build_dashboard_state(year=year, card_id=int(perk["card_id"]))
            self._send_json(state)
            return

        if path == "/api/cards":
            preset_key = body.get("preset_key", "custom")
            with get_db() as conn:
                conn.execute("UPDATE cards SET is_active = 0")
                if preset_key in PRESET_CARDS:
                    new_card_id = seed_card_from_preset(conn, preset_key, is_active=1)
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO cards (
                            name, preset_key, issuer, annual_fee, au_fee, au_count,
                            anniversary_date, color_theme, earning_summary, is_active
                        ) VALUES (?, 'custom', ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            body.get("name", "Custom Credit Card"),
                            body.get("issuer", "Custom"),
                            float(body.get("annual_fee", 0.0)),
                            float(body.get("au_fee", 0.0)),
                            int(body.get("au_count", 0)),
                            body.get("anniversary_date", "2026-11-01"),
                            body.get("color_theme", "emerald"),
                            body.get("earning_summary", ""),
                        ),
                    )
                    new_card_id = int(cur.lastrowid)
                conn.commit()
            self._send_json(build_dashboard_state(card_id=new_card_id), HTTPStatus.CREATED)
            return

        if path == "/api/perks":
            card_id = int(body["card_id"])
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO perks (
                        card_id, name, category, credit_type, frequency,
                        period_amount, requires_activation, activation_done, caveats, sort_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        card_id,
                        body.get("name", "New Credit"),
                        body.get("category", "Travel"),
                        body.get("credit_type", "hard_credit"),
                        body.get("frequency", "monthly"),
                        float(body.get("period_amount", 10.0)),
                        int(bool(body.get("requires_activation", False))),
                        int(bool(body.get("activation_done", True))),
                        body.get("caveats", ""),
                        int(body.get("sort_order", 50)),
                    ),
                )
                conn.commit()
            self._send_json(build_dashboard_state(card_id=card_id), HTTPStatus.CREATED)
            return

        if path == "/api/webhook/notify":
            year = int(body.get("year", dt.date.today().year))
            card_id = int(body["card_id"]) if body.get("card_id") else None
            digest = build_webhook_digest(year=year, card_id=card_id)
            webhook_url = body.get("webhook_url", digest["webhook_url"]).strip()
            send_now = bool(body.get("send", False))
            dispatch_result = (
                dispatch_webhook(webhook_url, digest["message"])
                if send_now
                else {"sent": False, "reason": "Preview mode"}
            )
            self._send_json({**digest, "dispatch": dispatch_result})
            return

        if path == "/api/import":
            with get_db() as conn:
                conn.execute("DELETE FROM usage_logs")
                conn.execute("DELETE FROM perks")
                conn.execute("DELETE FROM cards")
                for c in body.get("cards", []):
                    conn.execute(
                        """
                        INSERT INTO cards (
                            id, name, preset_key, issuer, annual_fee, au_fee, au_count,
                            anniversary_date, color_theme, earning_summary, webhook_url, include_lifestyle_in_roi, is_active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            c["id"],
                            c["name"],
                            c.get("preset_key", "custom"),
                            c.get("issuer", "Chase"),
                            c["annual_fee"],
                            c.get("au_fee", 0.0),
                            c.get("au_count", 0),
                            c.get("anniversary_date", "2026-11-01"),
                            c.get("color_theme", "sapphire"),
                            c.get("earning_summary", ""),
                            c.get("webhook_url", ""),
                            c.get("include_lifestyle_in_roi", 0),
                            c.get("is_active", 1),
                        ),
                    )
                for p in body.get("perks", []):
                    conn.execute(
                        """
                        INSERT INTO perks (
                            id, card_id, name, category, credit_type, frequency,
                            period_amount, requires_activation, activation_done, is_enabled, caveats, sort_order
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            p["id"],
                            p["card_id"],
                            p["name"],
                            p["category"],
                            p["credit_type"],
                            p["frequency"],
                            p["period_amount"],
                            p.get("requires_activation", 0),
                            p.get("activation_done", 0),
                            p.get("is_enabled", 1),
                            p.get("caveats", ""),
                            p.get("sort_order", 50),
                        ),
                    )
                for u in body.get("usage_logs", []):
                    conn.execute(
                        """
                        INSERT INTO usage_logs (perk_id, year, period_key, used_amount, used_date, notes)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            u["perk_id"],
                            u["year"],
                            u["period_key"],
                            u["used_amount"],
                            u.get("used_date", ""),
                            u.get("notes", ""),
                        ),
                    )
                conn.commit()
            self._send_json(build_dashboard_state())
            return

        self._send_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)

    def do_PUT(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        if path.startswith("/api/cards/"):
            card_id = int(path.split("/")[-1])
            with get_db() as conn:
                existing = conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
                if not existing:
                    self._send_json({"error": "Card not found"}, HTTPStatus.NOT_FOUND)
                    return

                if body.get("set_active"):
                    conn.execute("UPDATE cards SET is_active = 0")
                    conn.execute("UPDATE cards SET is_active = 1 WHERE id = ?", (card_id,))

                conn.execute(
                    """
                    UPDATE cards SET
                        name = ?,
                        annual_fee = ?,
                        au_fee = ?,
                        au_count = ?,
                        anniversary_date = ?,
                        webhook_url = ?,
                        include_lifestyle_in_roi = ?
                    WHERE id = ?
                    """,
                    (
                        body.get("name", existing["name"]),
                        float(body.get("annual_fee", existing["annual_fee"])),
                        float(body.get("au_fee", existing["au_fee"])),
                        int(body.get("au_count", existing["au_count"])),
                        body.get("anniversary_date", existing["anniversary_date"]),
                        body.get("webhook_url", existing["webhook_url"]),
                        int(
                            bool(
                                body.get(
                                    "include_lifestyle_in_roi",
                                    existing["include_lifestyle_in_roi"],
                                )
                            )
                        ),
                        card_id,
                    ),
                )
                conn.commit()
            year = int(body.get("year", dt.date.today().year))
            t_card_id = int(body["return_card_id"]) if body.get("return_card_id") else card_id
            self._send_json(build_dashboard_state(year=year, card_id=t_card_id))
            return

        if path.startswith("/api/perks/"):
            perk_id = int(path.split("/")[-1])
            with get_db() as conn:
                existing = conn.execute("SELECT * FROM perks WHERE id = ?", (perk_id,)).fetchone()
                if not existing:
                    self._send_json({"error": "Perk not found"}, HTTPStatus.NOT_FOUND)
                    return

                conn.execute(
                    """
                    UPDATE perks SET
                        name = ?,
                        category = ?,
                        credit_type = ?,
                        frequency = ?,
                        period_amount = ?,
                        requires_activation = ?,
                        activation_done = ?,
                        is_enabled = ?,
                        caveats = ?
                    WHERE id = ?
                    """,
                    (
                        body.get("name", existing["name"]),
                        body.get("category", existing["category"]),
                        body.get("credit_type", existing["credit_type"]),
                        body.get("frequency", existing["frequency"]),
                        float(body.get("period_amount", existing["period_amount"])),
                        int(bool(body.get("requires_activation", existing["requires_activation"]))),
                        int(bool(body.get("activation_done", existing["activation_done"]))),
                        int(bool(body.get("is_enabled", existing["is_enabled"]))),
                        body.get("caveats", existing["caveats"]),
                        perk_id,
                    ),
                )
                conn.commit()
            year = int(body.get("year", dt.date.today().year))
            return_cid = (
                int(body["return_card_id"])
                if body.get("return_card_id")
                else int(existing["card_id"])
            )
            self._send_json(build_dashboard_state(year=year, card_id=return_cid))
            return

        self._send_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/perks/"):
            perk_id = int(path.split("/")[-1])
            with get_db() as conn:
                existing = conn.execute("SELECT * FROM perks WHERE id = ?", (perk_id,)).fetchone()
                if not existing:
                    self._send_json({"error": "Perk not found"}, HTTPStatus.NOT_FOUND)
                    return
                card_id = int(existing["card_id"])
                conn.execute("DELETE FROM perks WHERE id = ?", (perk_id,))
                conn.commit()
            self._send_json(build_dashboard_state(card_id=card_id))
            return

        if path.startswith("/api/cards/"):
            card_id = int(path.split("/")[-1])
            with get_db() as conn:
                total_cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
                if total_cards <= 1:
                    self._send_json(
                        {"error": "Cannot delete the last remaining card."},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
                conn.execute(
                    "UPDATE cards SET is_active = 1 WHERE id = (SELECT id FROM cards LIMIT 1)"
                )
                conn.commit()
            self._send_json(build_dashboard_state())
            return

        self._send_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)


def run_server(host: str = "0.0.0.0", port: int = 8795) -> None:
    init_db()
    server = ThreadingHTTPServer((host, port), TrackerHTTPRequestHandler)
    print(f"✨ Credit Card Tracker running at http://{host}:{port} (Syncs on first open of day)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Self-Hosted Credit Card Tracker")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8795")))
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
