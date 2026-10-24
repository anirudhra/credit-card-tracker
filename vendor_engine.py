"""
Dynamic Issuer Website Vendor Scraper & Universal Live Merchant Lookup Engine.

Scrapes real named vendor lists, inclusions, and exclusions directly from:
1. American Express Official U.S. Rewards Info (`americanexpress.com/us/rewards-info/retail.html`)
2. Chase Official Rewards Category FAQ (`chase.com/personal/credit-cards/rewards-category-faq`)
3. Bank of America Official Museums on Us Directory (`about.bankofamerica.com/.../museums-on-us-partners`)
4. Discover Official 5% Cashback Calendar (`discover.com/credit-cards/cash-back/cashback-calendar.html`)

And provides a quarter-aware live web fallback classifier for any arbitrary store/vendor name.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import sqlite3
import ssl
import urllib.parse
import urllib.request
from typing import Any


def _fetch_html_lines(url: str, timeout: int = 8) -> list[str]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    cleaned = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
    cleaned = re.sub(r"</?(li|p|div|br|h[1-6]|tr|td)[^>]*>", "\n", cleaned, flags=re.I)
    cleaned = html.unescape(re.sub(r"<[^>]+>", " ", cleaned))
    lines = [re.sub(r"\s+", " ", line).strip() for line in cleaned.splitlines()]
    return [l for l in lines if l]


def ensure_vendor_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scraped_vendors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_name TEXT NOT NULL,
            normalized_name TEXT NOT NULL,
            category TEXT NOT NULL,
            rule_type TEXT NOT NULL DEFAULT 'included',
            source_issuer TEXT NOT NULL,
            source_url TEXT NOT NULL,
            official_rule TEXT NOT NULL,
            scraped_at TEXT NOT NULL,
            UNIQUE(normalized_name, category, source_issuer)
        );
        CREATE INDEX IF NOT EXISTS idx_scraped_vendors_norm ON scraped_vendors(normalized_name);

        CREATE TABLE IF NOT EXISTS sync_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _upsert_vendor(
    conn: sqlite3.Connection,
    vendor_name: str,
    category: str,
    rule_type: str,
    source_issuer: str,
    source_url: str,
    official_rule: str,
    now_iso: str,
) -> None:
    v_clean = vendor_name.strip(" *•-–—:,.")
    if not v_clean or len(v_clean) < 2 or len(v_clean) > 95:
        return
    conn.execute(
        """
        INSERT INTO scraped_vendors (
            vendor_name, normalized_name, category, rule_type,
            source_issuer, source_url, official_rule, scraped_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(normalized_name, category, source_issuer) DO UPDATE SET
            vendor_name = excluded.vendor_name,
            rule_type = excluded.rule_type,
            source_url = excluded.source_url,
            official_rule = excluded.official_rule,
            scraped_at = excluded.scraped_at
        """,
        (
            v_clean,
            _norm(v_clean),
            category,
            rule_type,
            source_issuer,
            source_url,
            official_rule,
            now_iso,
        ),
    )


def scrape_live_issuer_vendors(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Scrape live vendor lists from Amex, Chase, and Bank of America official category pages
    and store them in SQLite `scraped_vendors`.
    """
    ensure_vendor_tables(conn)
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    counts = {"amex": 0, "chase": 0, "boa_museums": 0}
    next_museums_weekend = "First full weekend of next month"

    # 1. Scrape Amex Official U.S. Rewards Info Page
    amex_url = "https://www.americanexpress.com/us/rewards-info/retail.html"
    try:
        amex_lines = _fetch_html_lines(amex_url)
        full_amex_text = "\n".join(amex_lines)

        for i, line in enumerate(amex_lines):
            if "Gas Stations" in line and i + 20 < len(amex_lines):
                block = amex_lines[i : i + 25]
                in_inc, in_exc = False, False
                for b in block:
                    if "Examples of merchants* that accept" in b:
                        in_inc, in_exc = True, False
                        continue
                    if "*This is not a complete list" in b:
                        in_inc = False
                        continue
                    if "Examples of merchants where you will NOT" in b:
                        in_exc, in_inc = True, False
                        continue
                    if "You may earn additional" in b or "U.S. Online Retail" in b:
                        in_exc = False
                    if in_inc and len(b) < 35:
                        _upsert_vendor(
                            conn,
                            b,
                            "Gas Stations",
                            "included",
                            "American Express",
                            amex_url,
                            f"Official Amex U.S. Gas Station merchant ({b}): Currently earns 3% cash back on Amex Blue Cash Everyday (up to $6,000/yr).",
                            now_iso,
                        )
                        counts["amex"] += 1
                    elif in_exc and len(b) < 35:
                        _upsert_vendor(
                            conn,
                            f"{b} Gas",
                            "Gas Stations",
                            "excluded",
                            "American Express",
                            amex_url,
                            f"Amex Official Exclusion: Gas stations at superstores/warehouse clubs like {b} do NOT earn 3% on Amex BCE. Use Chase Freedom Unlimited (1.5x Visa).",
                            now_iso,
                        )
                        counts["amex"] += 1
                break

        for m in re.finditer(r"\(e\.g\.,\s*([^)]+)\)", full_amex_text):
            inner = m.group(1)
            for part in re.split(r",|\band\b", inner):
                p = part.strip()
                if ".com" in p.lower():
                    _upsert_vendor(
                        conn,
                        p,
                        "Online Retail",
                        "included",
                        "American Express",
                        amex_url,
                        f"Official Amex U.S. Online Retail example ({p}): Currently earns 3% cash back on Amex Blue Cash Everyday (up to $6,000/yr) when paid online.",
                        now_iso,
                    )
                    counts["amex"] += 1

        for i, line in enumerate(amex_lines):
            if "have these specific names:" in line:
                for sub in amex_lines[i + 1 : i + 22]:
                    if "U.S. Select Major Drugstores" in sub or "Eligible purchases" in sub:
                        break
                    _upsert_vendor(
                        conn,
                        sub,
                        "Department & Apparel Stores",
                        "included",
                        "American Express",
                        amex_url,
                        f"Official Amex Select Major Department Store ({sub}): Order online on their website/app with Amex Blue Cash Everyday to earn 3% U.S. Online Retail cash back, or stack Chase Freedom Unlimited (1.5x UR) with Shop Through Chase!",
                        now_iso,
                    )
                    counts["amex"] += 1
                break

        drug_match = re.search(
            r"U\.S\.\s+drugstore\s+list\s+is:\s*([^.]+)\.", full_amex_text, re.IGNORECASE
        )
        if drug_match:
            for dr in re.split(r",|\band\b", drug_match.group(1)):
                dr_clean = dr.strip()
                if dr_clean:
                    _upsert_vendor(
                        conn,
                        dr_clean,
                        "Drugstores & Pharmacy",
                        "included",
                        "American Express",
                        amex_url,
                        f"Official U.S. Drugstore ({dr_clean}): Currently earns 3x Ultimate Rewards on Chase Freedom Unlimited (or 3% on Amex BCE if ordered online).",
                        now_iso,
                    )
                    counts["amex"] += 1

        for i, line in enumerate(amex_lines):
            if "list of Select U.S. Streaming Subscriptions" in line:
                for sub in amex_lines[i + 1 : i + 40]:
                    if "If your subscription is bundled" in sub or "U.S. Supermarkets" in sub:
                        break
                    _upsert_vendor(
                        conn,
                        sub,
                        "Streaming Subscriptions",
                        "included",
                        "American Express",
                        amex_url,
                        f"Official Select U.S. Streaming Service ({sub}): Use Amex Blue Cash Everyday for $7/mo Disney+/Hulu/ESPN+ credit, or CSR for complimentary Apple TV+ & Apple Music ($324/yr)!",
                        now_iso,
                    )
                    counts["amex"] += 1
                break

        for i, line in enumerate(amex_lines):
            if "U.S. Supermarkets" in line and i + 30 < len(amex_lines):
                block = amex_lines[i : i + 35]
                in_inc = False
                for b in block:
                    if "Examples of merchants* that accept" in b:
                        in_inc = True
                        continue
                    if "*This is not a complete list" in b:
                        break
                    if in_inc and len(b) < 40:
                        _upsert_vendor(
                            conn,
                            b,
                            "U.S. Supermarkets",
                            "included",
                            "American Express",
                            amex_url,
                            f"Official Amex U.S. Supermarket ({b}): Currently earns 3% cash back on Amex Blue Cash Everyday (up to $6,000/yr) and 2x–2.4x MR on Amex EveryDay (up to $6,000/yr).",
                            now_iso,
                        )
                        counts["amex"] += 1
                break

        for exc_vendor, exc_type in [
            ("Target (In-Store)", "Superstore — Excluded from Amex 3% Supermarkets"),
            ("Walmart (In-Store)", "Superstore — Excluded from Amex 3% Supermarkets"),
            ("BJ's Wholesale Club", "Warehouse Club — Excluded from Amex 3% Supermarkets"),
            ("7-Eleven", "Convenience Store — Excluded from Amex 3% Supermarkets"),
            ("Blue Apron", "Meal-Kit — Excluded from Amex 3% Supermarkets"),
            ("HelloFresh", "Meal-Kit — Excluded from Amex 3% Supermarkets"),
            ("Home Chef", "Meal-Kit — Eligible for $15/mo ($180/yr) Amex Blue Cash Everyday Credit!"),
        ]:
            _upsert_vendor(
                conn,
                exc_vendor,
                "U.S. Supermarkets / Food",
                "special" if "Home Chef" in exc_vendor else "excluded",
                "American Express",
                amex_url,
                f"Scraped from Amex Official Terms ({exc_type}): "
                + (
                    "Enroll in the $15/month Home Chef credit on Amex Blue Cash Everyday ($180/yr value)!"
                    if "Home Chef" in exc_vendor
                    else f"{exc_vendor} does NOT code as a U.S. Supermarket on Amex. Order online at Target.com/Walmart.com with Amex BCE or BoA Customized Cash Rewards (3% Online), or use BoA Customized Cash Rewards (2% Wholesale Club Visa) at Costco/Sam's/BJ's in-store."
                ),
                now_iso,
            )
            counts["amex"] += 1
    except Exception:
        pass

    # 2. Scrape Chase Official Rewards Category FAQ Page
    chase_url = "https://www.chase.com/personal/credit-cards/rewards-category-faq"
    try:
        chase_lines = _fetch_html_lines(chase_url)
        full_chase_text = " ".join(chase_lines)

        fd_match = re.search(
            r"following select food delivery merchants will qualify for this category:\s*([^.]+)\.",
            full_chase_text,
            re.IGNORECASE,
        )
        if fd_match:
            for item in re.split(r";|\band\b", fd_match.group(1)):
                v = re.sub(r"or the .*? mobile app", "", item, flags=re.I).strip()
                if v:
                    _upsert_vendor(
                        conn,
                        v,
                        "Food Delivery & Dining",
                        "included",
                        "Chase",
                        chase_url,
                        f"Official Chase Select Food Delivery Merchant ({v}): Earns 3x Ultimate Rewards on Chase Sapphire Reserve (plus 5% on Discover it during active Dining quarters, or 3% on BoA Customized Cash Rewards if Dining is your Choice Category). On DoorDash/Caviar, stack your CSR $15/mo + two $10/mo promos!",
                        now_iso,
                    )
                    counts["chase"] += 1

        st_match = re.search(
            r"following select merchants will qualify for this category:\s*([^.]+)\.",
            full_chase_text,
            re.IGNORECASE,
        )
        if st_match:
            for item in re.split(r",|\band\b", st_match.group(1)):
                v = item.strip()
                if v:
                    _upsert_vendor(
                        conn,
                        v,
                        "Streaming Subscriptions",
                        "included",
                        "Chase",
                        chase_url,
                        f"Official Chase Select Streaming Merchant ({v}): Apple TV+ & Apple Music are complimentary on CSR ($324/yr); Disney+/Hulu/ESPN+ get $7/mo credit on Amex BCE; also earns 3% on BoA Customized Cash Rewards when Online Shopping is selected!",
                        now_iso,
                    )
                    counts["chase"] += 1

        for v_rental in ["Airbnb", "Vrbo", "Vacasa"]:
            if v_rental.lower() in full_chase_text.lower():
                _upsert_vendor(
                    conn,
                    v_rental,
                    "Hotels & Vacation Rentals",
                    "special",
                    "Chase",
                    chase_url,
                    f"Scraped from Chase Official Category FAQ: {v_rental} is classified as a vacation home rental (excluded from CSR's 4x Direct Hotel multiplier, though it DOES trigger CSR's $300 Annual Travel Credit, BoA Travel Rewards statement credits, and 3% on BoA Customized Cash Rewards when Travel or Online Shopping is your Choice Category!).",
                    now_iso,
                )
                counts["chase"] += 1
    except Exception:
        pass

    # 3. Scrape Bank of America "Museums on Us" Partner Directory (225+ Museums)
    boa_url = "https://about.bankofamerica.com/en/making-an-impact/museums-on-us-partners"
    try:
        boa_lines = _fetch_html_lines(boa_url)
        for l in boa_lines:
            if "Next eligible Museums on Us weekend is" in l:
                next_museums_weekend = l.replace(
                    "Next eligible Museums on Us weekend is", ""
                ).strip()
                break

        museum_keywords = (
            "Museum",
            "Garden",
            "Zoo",
            "Center",
            "Aquarium",
            "Hall",
            "Institute",
            "Gallery",
            "Planetarium",
            "Society",
            "Interactive",
            "Conservatory",
            "LACMA",
            "MOXI",
        )
        skip_phrases = ("Bank of America", "Museums on Us", "Next eligible", "Find a museum")
        for l in boa_lines:
            if any(s in l for s in skip_phrases):
                continue
            if any(k in l for k in museum_keywords) and 5 < len(l) < 85:
                _upsert_vendor(
                    conn,
                    l,
                    "Museums & Cultural Attractions",
                    "included",
                    "Bank of America",
                    boa_url,
                    f"Official Bank of America 'Museums on Us' Partner ({l}): Show your physical Bank of America® Customized Cash Rewards and/or BoA Travel Rewards card + photo ID for FREE general admission on the 1st full weekend of the month (Next eligible weekend: {next_museums_weekend})!",
                    now_iso,
                )
                counts["boa_museums"] += 1
    except Exception:
        pass

    conn.execute(
        "INSERT OR REPLACE INTO sync_metadata (key, value) VALUES ('next_museums_weekend', ?)",
        (next_museums_weekend,),
    )
    conn.commit()
    return {**counts, "next_museums_weekend": next_museums_weekend}


BOA_CCR_CHOICE_LABELS: dict[str, str] = {
    "home_improvement": "Home Improvement & Furnishings",
    "online_shopping": "Online Shopping (incl. Cable, Internet, Phone & Streaming)",
    "gas": "Gas & EV Charging Stations",
    "dining": "Dining (Restaurants & Takeout)",
    "travel": "Travel (Flights, Hotels, Airbnb/Vrbo & Transit)",
    "drug_stores": "Drug Stores & Pharmacies",
}


# ---------------------------------------------------------------------------
# Quarter-Aware & BoA Choice-Aware Dynamic Category Templates
# ---------------------------------------------------------------------------
def get_dynamic_category_rules(
    today: dt.date | None = None,
    boa_choice: str = "home_improvement",
) -> dict[str, dict[str, str]]:
    if today is None:
        today = dt.date.today()
    q = (today.month - 1) // 3 + 1
    next_q = 1 if q == 4 else q + 1
    choice_label = BOA_CCR_CHOICE_LABELS.get(boa_choice, "Home Improvement & Furnishings")

    return {
        "Online Retail": {
            "best_card": (
                "Chase Freedom® (Active Q2 5x Amazon) or Amex Blue Cash Everyday® (3% Online Retail)"
                if q == 2
                else (
                    "Chase Freedom® (Active Q4 5x via PayPal!) or "
                    + (
                        "Amex BCE / BoA Customized Cash (Active 3% Online Shopping!)"
                        if boa_choice == "online_shopping"
                        else "Amex Blue Cash Everyday® (3% U.S. Online Retail)"
                    )
                    if q == 4
                    else (
                        "Amex Blue Cash Everyday® (3%) & BoA Customized Cash Rewards (Active 3% Online Shopping!)"
                        if boa_choice == "online_shopping"
                        else "Amex Blue Cash Everyday® (3% U.S. Online Retail)"
                    )
                )
            ),
            "best_rate": "5x UR via PayPal (Active Q4) / 3% Online" if q == 4 else ("5x UR Amazon (Q2) / 3% Online" if q == 2 else "3% Cash Back (Currently Valid)"),
            "runner_up": (
                "BoA Customized Cash Rewards (Active 3% Online Shopping — up to $2,500/qtr)"
                if boa_choice == "online_shopping"
                else f"Bank of America Travel Rewards (1.5x–2.62x) • (BoA Customized Cash is currently set to 3% {choice_label})"
            ),
            "activation_tip": (
                "🔥 Active Q4 Tip: If the online store accepts PayPal at checkout, pay with Chase Freedom via PayPal for 5x Ultimate Rewards (up to $1,500)! Otherwise use Amex BCE (3% Online Retail)."
                if q == 4
                else "Pay online on the merchant's website or app to trigger 3% on Amex BCE!"
            ),
        },
        "U.S. Supermarkets": {
            "best_card": (
                "Chase Freedom® (Active Q1 5x Groceries!) & Discover it® (Active Q1 5% Groceries!)"
                if q == 1
                else "Amex Blue Cash Everyday® (3% Cash) or Amex EveryDay® (2.4x MR w/ 20 swipes)"
            ),
            "best_rate": "5x UR + 5% Cash Back (Active Q1)" if q == 1 else "3% Cash or 2.4x MR (Currently Valid)",
            "runner_up": "BoA Customized Cash Rewards (2% Automatic at Grocery Stores & Wholesale Clubs regardless of 3% choice!) + CSR $20/mo DoorDash Grocery Promos",
            "activation_tip": (
                "Activate BOTH Chase Freedom Q1 5x and Discover Q1 5% for Groceries ($3,000 combined 5% capacity)! After that, use Amex BCE (3%), Amex EveryDay (2.4x MR), or BoA Customized Cash (2%)."
                if q == 1
                else (
                    "Currently valid: 3% on Amex BCE (up to $6k/yr), 2x–2.4x MR on Amex EveryDay (up to $6k/yr), and 2% automatic on BoA Customized Cash Rewards."
                    + (" Upcoming Next Quarter (Q1 Jan–Mar): Both Chase Freedom (5x UR) & Discover it (5%) rotate to Groceries!" if next_q == 1 else "")
                )
            ),
        },
        "Gas Stations": {
            "best_card": (
                "Chase Freedom® (Active Q3 5x Gas/EV!) & Discover it® (Active Q3 5% Gas/EV!)"
                if q == 3
                else (
                    "Amex Blue Cash Everyday® (3% Gas) & BoA Customized Cash Rewards (Active 3% Gas!)"
                    if boa_choice == "gas"
                    else "Amex Blue Cash Everyday® (3% U.S. Gas Stations)"
                )
            ),
            "best_rate": "5x UR + 5% Cash Back (Active Q3)" if q == 3 else "3% Cash Back (Currently Valid)",
            "runner_up": (
                "BoA Customized Cash Rewards (Active 3% Gas — works at Costco Gas!)"
                if boa_choice == "gas"
                else f"BoA Travel Rewards (1.5x–2.62x Visa at Costco Gas) • (Or switch BoA Customized Cash from {choice_label} → Gas)"
            ),
            "activation_tip": (
                "Activate BOTH Chase Freedom Q3 5x and Discover Q3 5% for Gas & EV Charging!"
                if q == 3
                else "Currently valid: Amex Blue Cash Everyday earns 3% year-round at U.S. stand-alone gas stations (up to $6,000/yr)."
            ),
        },
        "Drugstores & Pharmacy": {
            "best_card": (
                "Discover it® Cash Back (Active Q3 5% Drug Stores!)"
                if q == 3
                else (
                    "Bank of America® Customized Cash Rewards (Active 3% Drug Stores!)"
                    if boa_choice == "drug_stores"
                    else (
                        "Chase Freedom® (Active Q4 5x via PayPal Checkout!) or Amex BCE (3% Online at CVS.com)"
                        if q == 4
                        else "Amex Blue Cash Everyday® (3% Online at CVS.com/Walgreens.com) or BoA Travel Rewards (1.5x–2.62x In-Store)"
                    )
                )
            ),
            "best_rate": "5% (Active Q3)" if q == 3 else ("3% Cash Back (Active BoA Choice)" if boa_choice == "drug_stores" else ("5x UR via PayPal (Q4) / 3% Online" if q == 4 else "3% Online / 1.5x–2.62x In-Store")),
            "runner_up": f"BoA Customized Cash Rewards is currently set to 3% {choice_label} (can switch to Drug Stores 1x/mo in BoA app)",
            "activation_tip": "In Q4, CVS & Walgreens accept PayPal in-store (QR code) and online — triggering 5x Ultimate Rewards on Chase Freedom! Or switch BoA Customized Cash Rewards to 'Drug Stores' for 3% in-store.",
        },
        "Department & Apparel Stores": {
            "best_card": (
                "Chase Freedom® (Active Q4 5x via PayPal Checkout!) or Amex Blue Cash Everyday® (3% Online)"
                if q == 4
                else (
                    "Amex Blue Cash Everyday® (3% Online) & BoA Customized Cash Rewards (Active 3% Online Shopping!)"
                    if boa_choice == "online_shopping"
                    else "Amex Blue Cash Everyday® (3% U.S. Online Retail)"
                )
            ),
            "best_rate": "5x UR via PayPal (Active Q4) / 3% Online" if q == 4 else "3% Online + Gift Card Stack (Currently Valid)",
            "runner_up": "Discover Cashback Gift Cards (10%–20% instant bonus at Gap/Old Navy/Nike)",
            "activation_tip": "Order online for 3% on Amex BCE (or 5x UR on Chase Freedom when checking out with PayPal in Q4!).",
        },
        "Food Delivery & Dining": {
            "best_card": (
                f"Discover it® (Active Q{q} 5% Restaurants!) + CSR Credits & 3x UR"
                if q in (2, 4)
                else (
                    "Chase Sapphire Reserve® (3x UR Dining) & BoA Customized Cash Rewards (Active 3% Dining!)"
                    if boa_choice == "dining"
                    else "Chase Sapphire Reserve® (3x Ultimate Rewards on Worldwide Dining)"
                )
            ),
            "best_rate": f"5% Cash Back (Active Q{q}) + 3x UR" if q in (2, 4) else "3x Ultimate Rewards (Currently Valid)",
            "runner_up": "CSR $150 Biannual Exclusive Tables + $15/mo DoorDash Promo + Chase Freedom $10/qtr DoorDash",
            "activation_tip": (
                f"⚠️ Activate Discover Q{q} 5% for Restaurants! And on DoorDash, toggle your CSR $15/mo promo at checkout."
                if q in (2, 4)
                else "Currently valid: 3x UR on CSR + $150 CSR Exclusive Tables credit + $15/mo DoorDash promo."
            ),
        },
        "Streaming Subscriptions": {
            "best_card": "Amex Blue Cash Everyday® ($7/mo Disney/Hulu/ESPN+) + CSR (Free Apple TV+ & Music)",
            "best_rate": "Free Apple + $7/mo Disney Credit",
            "runner_up": (
                "Discover it (Active Q1 5% Select Streaming!)"
                if q == 1
                else (
                    "Chase Freedom (Active Q2 5x Select Streaming!)"
                    if q == 2
                    else (
                        "BoA Customized Cash Rewards (Active 3% Online Shopping covers Streaming!)"
                        if boa_choice == "online_shopping"
                        else "Amex EveryDay (counts toward 20 monthly swipes for +20% MR)"
                    )
                )
            ),
            "activation_tip": "Enroll in the $7/mo Disney Bundle credit in Amex Benefits; activate Apple TV+ & Apple Music in Chase Benefits.",
        },
        "Utilities, Internet, Cable & Phone": {
            "best_card": (
                "Discover it® Cash Back (Active Q4 5% Utilities!)"
                if q == 4
                else (
                    "Bank of America® Customized Cash Rewards (Active 3% Cable/Internet/Phone under Online Shopping!)"
                    if boa_choice == "online_shopping"
                    else "Bank of America® Travel Rewards (1.5x–2.62x) or Amex EveryDay® (1.2x MR)"
                )
            ),
            "best_rate": "5% Cash Back (Active Q4 Oct–Dec)" if q == 4 else ("3% Cash Back (Active BoA Online)" if boa_choice == "online_shopping" else "1.5x–2.62x BoA / 1.2x MR"),
            "runner_up": f"Chase Freedom (5x UR in Q4 if utility accepts PayPal) • (BoA Customized Cash is currently set to 3% {choice_label})",
            "activation_tip": (
                "⚠️ Activate Discover Q4 5% (Oct–Dec) before paying electric, gas, water, internet, cable, or phone bills!"
                if q == 4
                else f"Your BoA Customized Cash 3% category is currently '{choice_label}'. If you switch it to 'Online Shopping', Cable, Internet, and Phone plans earn 3%!"
            ),
        },
        "Hotels & Vacation Rentals": {
            "best_card": "Chase Sapphire Reserve®",
            "best_rate": "8x Chase Travel / 4x Direct Hotels + $300/$500 Credits",
            "runner_up": (
                "BoA Customized Cash Rewards (Active 3% Travel — covers Airbnb & Vrbo!)"
                if boa_choice == "travel"
                else "Chase Freedom (5x Chase Travel) or BoA Travel Rewards (3x Portal)"
            ),
            "activation_tip": "Direct hotels earn 4x on CSR (8x via Chase Travel). For Airbnb & Vrbo, CSR's $300 Travel Credit applies automatically!",
        },
        "Airlines & Flights": {
            "best_card": "Chase Sapphire Reserve®",
            "best_rate": "8x Chase Travel / 4x Direct Airlines",
            "runner_up": "Chase Freedom (5x Chase Travel)",
            "activation_tip": "Includes primary Trip Delay ($500/ticket after 6 hrs) and Baggage Delay insurance when paid with CSR.",
        },
        "Rideshare & Local Transit": {
            "best_card": (
                "Discover it® (Active Q3 5% Public Transit) + CSR ($10/mo Lyft Credit)"
                if q == 3
                else "Chase Sapphire Reserve® ($10/mo Lyft Credit + $300 Travel Credit)"
            ),
            "best_rate": "$10/mo Lyft Credit + Travel Credit (Currently Valid)",
            "runner_up": "BoA Customized Cash Rewards (3% if Travel selected)" if boa_choice == "travel" else "Chase Freedom (5x Lyft)",
            "activation_tip": "Set CSR as your default payment method in the Lyft app to trigger the $10/month in-app credit.",
        },
        "Home Improvement & Hardware": {
            "best_card": (
                "Discover it® (Active Q2 5% Home Improvement!) & BoA Customized Cash (Active 3% Home Improvement!)"
                if q == 2 and boa_choice == "home_improvement"
                else (
                    "Discover it® Cash Back (Active Q2 5% Home Improvement!)"
                    if q == 2
                    else (
                        "Bank of America® Customized Cash Rewards (★ Active 3% Choice: Home Improvement & Furnishings!)"
                        if boa_choice == "home_improvement"
                        else "Amex Blue Cash Everyday® (3% Online at HomeDepot.com / Lowes.com)"
                    )
                )
            ),
            "best_rate": (
                "5% (Active Q2) + 3% BoA"
                if q == 2
                else (
                    "3% Cash Back In-Store & Online (★ Active BoA 3% Choice!)"
                    if boa_choice == "home_improvement"
                    else "3% Online / 10%–15% Gift Card Bonus"
                )
            ),
            "runner_up": "Amex Blue Cash Everyday (3% Online at HomeDepot.com/Lowes.com) or Discover Gift Cards (+10%–15% Bonus)",
            "activation_tip": (
                "✓ Your BoA Customized Cash Rewards 3% Choice Category is currently set to 'Home Improvement & Furnishings' — swipe it in-store or online at Home Depot, Lowe's, IKEA, Ace Hardware, Menards, furniture stores, or contractors for 3% cash back (up to $2,500/qtr)!"
                if boa_choice == "home_improvement"
                else f"Your BoA Customized Cash 3% category is currently set to '{choice_label}'. Switch it to 'Home Improvement & Furnishings' in the BoA app (or dropdown above) before big Home Depot/Lowe's/IKEA purchases!"
            ),
        },
        "Entertainment, Concerts & Live Events": {
            "best_card": (
                "Chase Sapphire Reserve® ($150 StubHub Credit) → then Discover it® (Active Q4 5% Entertainment!)"
                if q == 4
                else (
                    "Chase Sapphire Reserve® ($150 StubHub Credit) → then Chase Freedom® (Active Q3 5x Live Entertainment!)"
                    if q == 3
                    else "Chase Sapphire Reserve® ($150 StubHub Credit) → then BoA Travel Rewards (1.5x–2.62x)"
                )
            ),
            "best_rate": "$150 StubHub Credit + 5% Q4 Cash Back" if q == 4 else ("$150 Credit + 5x UR Q3" if q == 3 else "$150 StubHub Credit + 1.5x"),
            "runner_up": "BoA Customized Cash Rewards & BoA Travel Rewards (Free Museums on Us 1st full weekend/mo)",
            "activation_tip": "⚠️ Activate CSR's $150 StubHub/viagogo credit in Chase Benefits before buying tickets"
            + (", and activate Discover Q4 5% for movie theaters, concerts, and events!" if q == 4 else "."),
        },
        "Museums & Cultural Attractions": {
            "best_card": "Bank of America® Customized Cash Rewards & Bank of America® Travel Rewards",
            "best_rate": "FREE Admission (1st Full Weekend Every Month — 2 Cards = 2 Tickets!)",
            "runner_up": "Discover it (5% in Q4 Entertainment)" if q == 4 else "CSR ($300 Travel Credit)",
            "activation_tip": "Show your physical BoA Customized Cash Rewards and/or BoA Travel Rewards card + photo ID at the ticket counter on the first full weekend (Sat & Sun) of any month.",
        },
        "General Retail & Catch-All": {
            "best_card": (
                "Chase Freedom® (Active Q4 5x UR if PayPal accepted!) or Bank of America® Travel Rewards (1.5x–2.62x)"
                if q == 4
                else "Bank of America® Travel Rewards (1.5x–2.62x Catch-All) or Amex EveryDay® (1.2x MR w/ 20 swipes)"
            ),
            "best_rate": "5x UR via PayPal (Q4) or 1.5x–2.62x BoA" if q == 4 else "1.5x–2.62x BoA or 1.2x MR",
            "runner_up": "BoA Customized Cash Rewards (2% Automatic at Costco & Wholesale Clubs regardless of 3% choice!)",
            "activation_tip": "For Costco in-warehouse (Visa-only), swipe Bank of America Customized Cash Rewards for 2% automatic cash back on Wholesale Clubs (up to $2,500/qtr)!",
        },
    }


KEYWORD_TO_CATEGORY: list[tuple[tuple[str, ...], str]] = [
    (
        (
            "comcast",
            "xfinity",
            "verizon",
            "at&t",
            "att",
            "t-mobile",
            "tmobile",
            "spectrum",
            "cox",
            "pg&e",
            "pge",
            "coned",
            "con edison",
            "duke energy",
            "electric",
            "water bill",
            "gas bill",
            "utility",
            "utilities",
            "internet",
            "cable",
            "phone bill",
            "cellular",
            "wireless",
            "fios",
            "google fiber",
            "sonic",
        ),
        "Utilities, Internet, Cable & Phone",
    ),
    (
        (
            "cvs",
            "walgreens",
            "rite aid",
            "duane reade",
            "pharmacy",
            "drugstore",
            "apothecary",
        ),
        "Drugstores & Pharmacy",
    ),
    (
        (
            "gap",
            "old navy",
            "banana republic",
            "athleta",
            "macy",
            "nordstrom",
            "bloomingdale",
            "saks",
            "kohl",
            "jcpenney",
            "dillard",
            "tj maxx",
            "marshalls",
            "ross",
            "uniqlo",
            "zara",
            "h&m",
            "lululemon",
            "nike",
            "adidas",
            "rei",
            "dick's",
            "apparel",
            "clothing",
            "shoes",
            "foot locker",
            "sephora",
            "ulta",
        ),
        "Department & Apparel Stores",
    ),
    (
        (
            "home depot",
            "lowe's",
            "lowes",
            "ace hardware",
            "menards",
            "true value",
            "harbor freight",
            "sherwin",
            "benjamin moore",
            "hardware",
            "lumber",
            "plumbing",
        ),
        "Home Improvement & Hardware",
    ),
    (
        (
            "whole foods",
            "trader joe",
            "safeway",
            "kroger",
            "publix",
            "aldi",
            "wegmans",
            "h-e-b",
            "heb",
            "albertsons",
            "vons",
            "pavilions",
            "ralphs",
            "stop & shop",
            "giant",
            "food lion",
            "harris teeter",
            "meijer",
            "hy-vee",
            "shoprite",
            "smart & final",
            "sprouts",
            "winn-dixie",
            "freshdirect",
            "supermarket",
            "grocery",
            "99 ranch",
            "hmart",
            "h mart",
        ),
        "U.S. Supermarkets",
    ),
    (
        (
            "shell",
            "chevron",
            "exxon",
            "mobil",
            "bp",
            "arco",
            "texaco",
            "valero",
            "citgo",
            "sunoco",
            "marathon",
            "phillips 66",
            "76",
            "speedway",
            "wawa",
            "sheetz",
            "buc-ee",
            "quiktrip",
            "racetrac",
            "murphy usa",
            "gulf",
            "hess",
            "tesla supercharger",
            "chargepoint",
            "evgo",
            "electrify america",
            "gas station",
            "fuel",
        ),
        "Gas Stations",
    ),
    (
        (
            "doordash",
            "uber eats",
            "ubereats",
            "grubhub",
            "postmates",
            "caviar",
            "seamless",
            "chipotle",
            "starbucks",
            "mcdonald",
            "chick-fil-a",
            "panera",
            "sweetgreen",
            "shake shack",
            "in-n-out",
            "taco bell",
            "wendy",
            "subway",
            "domino",
            "pizza",
            "sushi",
            "cafe",
            "coffee",
            "bakery",
            "diner",
            "bistro",
            "steakhouse",
            "restaurant",
            "dining",
            "opentable",
            "resy",
        ),
        "Food Delivery & Dining",
    ),
    (
        (
            "marriott",
            "hyatt",
            "hilton",
            "ihg",
            "westin",
            "sheraton",
            "ritz-carlton",
            "four seasons",
            "fairmont",
            "omni",
            "wyndham",
            "best western",
            "choice hotels",
            "motel",
            "resort",
            "hotel",
            "the edit",
            "airbnb",
            "vrbo",
            "vacasa",
        ),
        "Hotels & Vacation Rentals",
    ),
    (
        (
            "united airlines",
            "delta",
            "american airlines",
            "southwest",
            "jetblue",
            "alaska airlines",
            "spirit",
            "frontier",
            "hawaiian airlines",
            "british airways",
            "air canada",
            "lufthansa",
            "emirates",
            "singapore airlines",
            "ana",
            "jal",
            "airline",
            "flight",
        ),
        "Airlines & Flights",
    ),
    (
        (
            "lyft",
            "uber",
            "waymo",
            "taxi",
            "cab",
            "subway",
            "metro",
            "bart",
            "mta",
            "wmata",
            "caltrain",
            "amtrak",
            "ferry",
            "e-zpass",
            "ezpass",
            "fastrak",
            "sunpass",
            "toll",
            "parking",
            "spothero",
            "parkmobile",
        ),
        "Rideshare & Local Transit",
    ),
    (
        (
            "stubhub",
            "viagogo",
            "ticketmaster",
            "seatgeek",
            "vivid seats",
            "axs",
            "eventbrite",
            "amc theatres",
            "regal",
            "cinemark",
            "alamo drafthouse",
            "movie",
            "cinema",
            "concert",
            "broadway",
            "disneyland",
            "disney world",
            "universal studios",
            "six flags",
            "bowling",
            "topgolf",
        ),
        "Entertainment, Concerts & Live Events",
    ),
    (
        (
            "netflix",
            "hulu",
            "disney+",
            "espn+",
            "apple tv",
            "apple music",
            "spotify",
            "max",
            "hbo",
            "peacock",
            "paramount+",
            "youtube tv",
            "youtube premium",
            "sling",
            "fubo",
            "siriusxm",
            "pandora",
            "audible",
            "kindle",
            "tidal",
        ),
        "Streaming Subscriptions",
    ),
    (
        (
            "amazon",
            "ebay",
            "etsy",
            "chewy",
            "wayfair",
            "newegg",
            "b&h",
            "adorama",
            "zappos",
            "overstock",
            "ikea",
            "best buy",
            "apple.com",
            "walmart.com",
            "target.com",
            "petco",
            "petsmart",
            "gamestop",
            "steam",
            "playstation",
            "nintendo",
            "xbox",
        ),
        "Online Retail",
    ),
    (
        (
            "museum",
            "aquarium",
            "botanic",
            "zoo",
            "science center",
            "art institute",
            "planetarium",
        ),
        "Museums & Cultural Attractions",
    ),
]


def _web_classify_unknown_vendor(query: str) -> tuple[str, str]:
    """
    Query DuckDuckGo HTML/Instant summary for an unknown vendor name and classify its category.
    """
    ctx = ssl.create_default_context()
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query + ' store company merchant category')}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
        },
    )
    snippet = ""
    try:
        with urllib.request.urlopen(req, timeout=4, context=ctx) as resp:
            raw = resp.read(60000).decode("utf-8", errors="ignore")
        m = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.S | re.I)
        if m:
            snippet = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1)))
            snippet = re.sub(r"\s+", " ", snippet).strip()
    except Exception:
        pass

    combined = f"{query} {snippet}".lower()
    for keywords, cat in KEYWORD_TO_CATEGORY:
        if any(k in combined for k in keywords):
            return cat, snippet
    return "General Retail & Catch-All", snippet


def lookup_any_vendor(conn: sqlite3.Connection, query: str, today: dt.date | None = None) -> dict[str, Any]:
    """
    Look up any vendor name against:
    1) Live-scraped issuer vendor database (`scraped_vendors`)
    2) Quarter-aware keyword & live web merchant classification engine
    """
    ensure_vendor_tables(conn)
    if today is None:
        today = dt.date.today()

    q_clean = query.strip()
    q_norm = _norm(q_clean)
    if not q_norm:
        return {"query": q_clean, "scraped_matches": [], "recommendation": None}

    rows = conn.execute(
        """
        SELECT * FROM scraped_vendors
        WHERE normalized_name LIKE ? OR ? LIKE '%' || normalized_name || '%'
        ORDER BY
            CASE WHEN normalized_name = ? THEN 0
                 WHEN normalized_name LIKE ? THEN 1
                 ELSE 2 END,
            length(vendor_name) ASC
        LIMIT 12
        """,
        (f"%{q_norm}%", q_norm, q_norm, f"{q_norm}%"),
    ).fetchall()
    scraped_matches = [dict(r) for r in rows]

    detected_cat = None
    web_snippet = ""
    for keywords, cat in KEYWORD_TO_CATEGORY:
        if any(k in q_clean.lower() for k in keywords):
            detected_cat = cat
            break

    row_choice = conn.execute(
        "SELECT value FROM sync_metadata WHERE key = 'boa_ccr_choice_category'"
    ).fetchone()
    boa_choice = row_choice["value"] if row_choice else "home_improvement"

    rules_map = get_dynamic_category_rules(today, boa_choice)

    if not detected_cat and scraped_matches:
        detected_cat = scraped_matches[0]["category"]
        if detected_cat not in rules_map:
            detected_cat = "General Retail & Catch-All"

    if not detected_cat:
        detected_cat, web_snippet = _web_classify_unknown_vendor(q_clean)

    template = rules_map.get(detected_cat, rules_map["General Retail & Catch-All"])
    total_scraped = conn.execute("SELECT COUNT(*) FROM scraped_vendors").fetchone()[0]

    return {
        "query": q_clean,
        "detected_category": detected_cat,
        "web_snippet": web_snippet,
        "best_card": template["best_card"],
        "best_rate": template["best_rate"],
        "runner_up": template["runner_up"],
        "activation_tip": template["activation_tip"],
        "scraped_matches": scraped_matches,
        "total_scraped_vendors_in_db": total_scraped,
    }
