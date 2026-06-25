"""Exchange rate fetcher with on-disk caching and graceful fallback."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from config import SETTINGS

log = logging.getLogger(__name__)

API_URL = "https://v6.exchangerate-api.com/v6/{key}/latest/{base}"
FALLBACK_URL = "https://api.exchangerate-api.com/v4/latest/{base}"  # public, no key
TIMEOUT = 10  # seconds


# --- ISO 4217 currency metadata (codes, names, symbols, emoji flags) -----
# A curated set of all actively circulating ISO 4217 currencies plus common
# precious metals (XAU, XAG, XPT, XPD). Codes marked "ZZZ" intentionally map
# to a placeholder so unknown codes are still handled gracefully.
CURRENCY_NAMES: dict[str, str] = {
    "AED": "UAE Dirham", "AFN": "Afghan Afghani", "ALL": "Albanian Lek",
    "AMD": "Armenian Dram", "ANG": "Netherlands Antillean Guilder",
    "AOA": "Angolan Kwanza", "ARS": "Argentine Peso", "AUD": "Australian Dollar",
    "AWG": "Aruban Florin", "AZN": "Azerbaijani Manat",
    "BAM": "Bosnia-Herzegovina Mark", "BBD": "Barbadian Dollar",
    "BDT": "Bangladeshi Taka", "BGN": "Bulgarian Lev", "BHD": "Bahraini Dinar",
    "BIF": "Burundian Franc", "BMD": "Bermudan Dollar", "BND": "Brunei Dollar",
    "BOB": "Bolivian Boliviano", "BRL": "Brazilian Real", "BSD": "Bahamian Dollar",
    "BTN": "Bhutanese Ngultrum", "BWP": "Botswanan Pula", "BYN": "Belarusian Ruble",
    "BZD": "Belize Dollar",
    "CAD": "Canadian Dollar", "CDF": "Congolese Franc", "CHF": "Swiss Franc",
    "CLP": "Chilean Peso", "CNY": "Chinese Yuan", "COP": "Colombian Peso",
    "CRC": "Costa Rican Colón", "CUP": "Cuban Peso", "CVE": "Cape Verdean Escudo",
    "CZK": "Czech Koruna",
    "DJF": "Djiboutian Franc", "DKK": "Danish Krone", "DOP": "Dominican Peso",
    "DZD": "Algerian Dinar",
    "EGP": "Egyptian Pound", "ERN": "Eritrean Nakfa", "ETB": "Ethiopian Birr",
    "EUR": "Euro",
    "FJD": "Fijian Dollar", "FKP": "Falkland Pound",
    "GBP": "British Pound", "GEL": "Georgian Lari", "GHS": "Ghanaian Cedi",
    "GIP": "Gibraltar Pound", "GMD": "Gambian Dalasi", "GNF": "Guinean Franc",
    "GTQ": "Guatemalan Quetzal", "GYD": "Guyanaese Dollar",
    "HKD": "Hong Kong Dollar", "HNL": "Honduran Lempira", "HRK": "Croatian Kuna",
    "HTG": "Haitian Gourde", "HUF": "Hungarian Forint",
    "IDR": "Indonesian Rupiah", "ILS": "Israeli New Shekel", "INR": "Indian Rupee",
    "IQD": "Iraqi Dinar", "IRR": "Iranian Rial", "ISK": "Icelandic Króna",
    "JMD": "Jamaican Dollar", "JOD": "Jordanian Dinar", "JPY": "Japanese Yen",
    "KES": "Kenyan Shilling", "KGS": "Kyrgystani Som", "KHR": "Cambodian Riel",
    "KMF": "Comorian Franc", "KPW": "North Korean Won", "KRW": "South Korean Won",
    "KWD": "Kuwaiti Dinar", "KYD": "Cayman Islands Dollar", "KZT": "Kazakhstani Tenge",
    "LAK": "Laotian Kip", "LBP": "Lebanese Pound", "LKR": "Sri Lankan Rupee",
    "LRD": "Liberian Dollar", "LSL": "Lesotho Loti", "LYD": "Libyan Dinar",
    "MAD": "Moroccan Dirham", "MDL": "Moldovan Leu", "MGA": "Malagasy Ariary",
    "MKD": "Macedonian Denar", "MMK": "Myanmar Kyat", "MNT": "Mongolian Tugrik",
    "MOP": "Macanese Pataca", "MRU": "Mauritanian Ouguiya", "MUR": "Mauritian Rupee",
    "MVR": "Maldivian Rufiyaa", "MWK": "Malawian Kwacha", "MXN": "Mexican Peso",
    "MYR": "Malaysian Ringgit", "MZN": "Mozambican Metical",
    "NAD": "Namibian Dollar", "NGN": "Nigerian Naira", "NIO": "Nicaraguan Córdoba",
    "NOK": "Norwegian Krone", "NPR": "Nepalese Rupee", "NZD": "New Zealand Dollar",
    "OMR": "Omani Rial",
    "PAB": "Panamanian Balboa", "PEN": "Peruvian Sol", "PGK": "Papua New Guinean Kina",
    "PHP": "Philippine Peso", "PKR": "Pakistani Rupee", "PLN": "Polish Złoty",
    "PYG": "Paraguayan Guarani",
    "QAR": "Qatari Riyal",
    "RON": "Romanian Leu", "RSD": "Serbian Dinar", "RUB": "Russian Ruble",
    "RWF": "Rwandan Franc",
    "SAR": "Saudi Riyal", "SBD": "Solomon Islands Dollar", "SCR": "Seychellois Rupee",
    "SDG": "Sudanese Pound", "SEK": "Swedish Krona", "SGD": "Singapore Dollar",
    "SHP": "Saint Helena Pound", "SLE": "Sierra Leonean Leone (new)",
    "SOS": "Somali Shilling", "SRD": "Surinamese Dollar", "SSP": "South Sudanese Pound",
    "STN": "São Tomé and Príncipe Dobra", "SVC": "Salvadoran Colón", "SYP": "Syrian Pound",
    "SZL": "Eswatini Lilangeni",
    "THB": "Thai Baht", "TJS": "Tajikistani Somoni", "TMT": "Turkmenistani Manat",
    "TND": "Tunisian Dinar", "TOP": "Tongan Paʻanga", "TRY": "Turkish Lira",
    "TTD": "Trinidad and Tobago Dollar", "TVD": "Tuvaluan Dollar", "TWD": "Taiwan Dollar",
    "TZS": "Tanzanian Shilling",
    "UAH": "Ukrainian Hryvnia", "UGX": "Ugandan Shilling", "USD": "United States Dollar",
    "UYU": "Uruguayan Peso", "UZS": "Uzbekistani Som",
    "VES": "Venezuelan Bolívar Soberano", "VND": "Vietnamese Đồng", "VUV": "Vanuatu Vatu",
    "WST": "Samoan Tala",
    "XAF": "Central African CFA Franc", "XCD": "East Caribbean Dollar",
    "XCG": "Caribbean Guilder", "XDR": "IMF Special Drawing Rights",
    "XOF": "West African CFA Franc", "XPF": "CFP Franc",
    "YER": "Yemeni Rial",
    "ZAR": "South African Rand", "ZMW": "Zambian Kwacha", "ZWG": "Zimbabwean Gold",
    "XAU": "Gold (troy ounce)", "XAG": "Silver (troy ounce)",
    "XPT": "Platinum (troy ounce)", "XPD": "Palladium (troy ounce)",
}

CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "INR": "₹",
    "KRW": "₩", "RUB": "₽", "BRL": "R$", "ZAR": "R", "TRY": "₺", "NGN": "₦",
    "VND": "₫", "THB": "฿", "MXN": "$", "PHP": "₱", "IDR": "Rp", "MYR": "RM",
    "PLN": "zł", "CZK": "Kč", "HUF": "Ft", "ILS": "₪", "CLP": "$", "COP": "$",
    "ARS": "$", "PEN": "S/", "CHF": "Fr", "SEK": "kr", "NOK": "kr", "DKK": "kr",
    "ISK": "kr", "TWD": "NT$", "SAR": "﷼", "AED": "د.إ", "EGP": "£", "PKR": "₨",
    "BDT": "৳", "LKR": "₨", "NPR": "₨", "UAH": "₴", "KZT": "₸", "GEL": "₾",
    "AZN": "₼", "KGS": "с", "MDL": "L", "AMD": "֏", "GHS": "₵", "KES": "Sh",
    "UGX": "Sh", "TZS": "Sh", "ETB": "Br", "MAD": "د.م.", "TND": "د.ت",
    "DZD": "د.ج", "LYD": "ل.د", "IQD": "ع.د", "JOD": "د.ا", "LBP": "ل.ل",
    "SYP": "£", "YER": "﷼", "OMR": "﷼", "QAR": "﷼", "BHD": ".د.ب", "KWD": "د.ك",
    "XAF": "Fr", "XOF": "Fr", "XPF": "Fr", "AUD": "A$", "NZD": "NZ$", "CAD": "C$",
    "HKD": "HK$", "SGD": "S$", "FJD": "FJ$", "WST": "T", "TOP": "T$",
    "XAU": "oz", "XAG": "oz", "XPT": "oz", "XPD": "oz",
}


def name_of(code: str) -> str:
    return CURRENCY_NAMES.get(code.upper(), code.upper())


def symbol_of(code: str) -> str:
    return CURRENCY_SYMBOLS.get(code.upper(), code.upper())


def flag_emoji(code: str) -> str:
    """Best-effort regional indicator emoji for an ISO 4217 currency code.

    Not every currency maps to a single country, so this is a UX nicety only.
    """
    mapping = {
        "USD": "US", "EUR": "EU", "GBP": "GB", "JPY": "JP", "CNY": "CN",
        "INR": "IN", "KRW": "KR", "RUB": "RU", "BRL": "BR", "ZAR": "ZA",
        "TRY": "TR", "NGN": "NG", "VND": "VN", "THB": "TH", "MXN": "MX",
        "PHP": "PH", "IDR": "ID", "MYR": "MY", "PLN": "PL", "CZK": "CZ",
        "HUF": "HU", "ILS": "IL", "CHF": "CH", "SEK": "SE", "NOK": "NO",
        "DKK": "DK", "CAD": "CA", "AUD": "AU", "NZD": "NZ", "HKD": "HK",
        "SGD": "SG", "SAR": "SA", "AED": "AE", "EGP": "EG", "PKR": "PK",
        "BDT": "BD", "ARS": "AR", "CLP": "CL", "COP": "CO", "PEN": "PE",
        "UAH": "UA", "KES": "KE", "GHS": "GH", "TZS": "TZ", "UGX": "UG",
        "ETB": "ET", "MAD": "MA", "DZD": "DZ", "TND": "TN", "IQD": "IQ",
        "JOD": "JO", "LBP": "LB", "SYP": "SY", "YER": "YE", "OMR": "OM",
        "QAR": "QA", "BHD": "BH", "KWD": "KW", "XAF": "CM", "XOF": "SN",
        "KZT": "KZ", "GEL": "GE", "AZN": "AZ", "AMD": "AM", "MDL": "MD",
        "ALL": "AL", "BGN": "BG", "HRK": "HR", "RSD": "RS", "RON": "RO",
        "BYN": "BY", "ISK": "IS", "TWD": "TW", "LKR": "LK", "NPR": "NP",
        "MMK": "MM", "KHR": "KH", "LAK": "LA", "MNT": "MN", "AFN": "AF",
        "UZS": "UZ", "TJS": "TJ", "TMT": "TM", "KGS": "KG",
    }
    cc = mapping.get(code.upper())
    if not cc:
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc)


# ----- disk cache helpers ----------------------------------------------------

def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        log.warning("Corrupt cache at %s, ignoring", path)
        return default


def _write_json(path, payload) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_cached_rates() -> tuple[dict[str, float], dict[str, Any]]:
    rates = _read_json(SETTINGS.rates_file, {})
    meta = _read_json(SETTINGS.meta_file, {})
    return rates or {}, meta or {}


def read_meta() -> dict[str, Any]:
    """Public accessor for the rate-cache metadata block."""
    return _read_json(SETTINGS.meta_file, {}) or {}


# ----- fetch ----------------------------------------------------------------

def _fetch_primary(base: str) -> dict[str, float]:
    """Authenticated exchangerate-api.com v6 endpoint."""
    if not SETTINGS.exchange_api_key:
        raise RuntimeError("EXCHANGE_API_KEY not set")
    url = API_URL.format(key=SETTINGS.exchange_api_key, base=base)
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("result") != "success":
        raise RuntimeError(f"API error: {payload.get('error-type', 'unknown')}")
    rates = payload.get("conversion_rates") or {}
    if not rates:
        raise RuntimeError("Empty rates payload from primary API")
    return rates


def _fetch_fallback(base: str) -> dict[str, float]:
    """No-auth open exchangerate-api.com v4 endpoint."""
    url = FALLBACK_URL.format(base=base)
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    rates = (resp.json() or {}).get("rates")
    if not rates:
        raise RuntimeError("Empty rates payload from fallback API")
    return rates


def refresh_rates(force: bool = False) -> dict[str, float]:
    """Fetch fresh rates, persist to disk, and return the rate map.

    Falls back to the public endpoint if the primary fails. If both fail and
    we already have a cached set, we keep using the cache (refresh is best
    effort; the bot should keep serving conversions).
    """
    base = SETTINGS.base_currency
    last_meta = _read_json(SETTINGS.meta_file, {})
    age_min = (time.time() - float(last_meta.get("fetched_at", 0))) / 60.0 if last_meta else float("inf")

    if not force and age_min < SETTINGS.refresh_minutes and _read_json(SETTINGS.rates_file, {}):
        return _read_json(SETTINGS.rates_file, {})

    rates: dict[str, float] | None = None
    used_provider = None
    last_error: Exception | None = None

    for provider in (_fetch_primary, _fetch_fallback):
        try:
            rates = provider(base)
            used_provider = provider.__name__
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.warning("Rate provider %s failed: %s", provider.__name__, exc)

    if not rates:
        cached = _read_json(SETTINGS.rates_file, {})
        if cached:
            log.error("All providers failed; serving cached rates (%s)", last_error)
            return cached
        raise RuntimeError(f"All rate providers failed and no cache available: {last_error}")

    _write_json(SETTINGS.rates_file, rates)
    _write_json(SETTINGS.meta_file, {
        "fetched_at": time.time(),
        "base": base,
        "provider": used_provider,
        "count": len(rates),
    })
    log.info("Refreshed %d rates from %s", len(rates), used_provider)
    return rates


# ----- conversion -----------------------------------------------------------

def convert(amount: float, from_ccy: str, to_ccy: str,
            rates: dict[str, float]) -> float | None:
    """Convert `amount` between two currencies given rates keyed on `to_ccy`,
    expressed against SETTINGS.base_currency. Returns None if unsupported.
    """
    from_ccy = from_ccy.upper()
    to_ccy = to_ccy.upper()
    if from_ccy == to_ccy:
        return amount

    base = SETTINGS.base_currency
    if base not in rates:
        # Rates are already in units of `base`, so this should always hold.
        return None

    # rates[X] = how many X equals 1 base unit
    if from_ccy == base:
        rate = rates.get(to_ccy)
        if rate is None:
            return None
        return amount * rate
    if to_ccy == base:
        rate = rates.get(from_ccy)
        if rate is None:
            return None
        return amount / rate
    a = rates.get(from_ccy)
    b = rates.get(to_ccy)
    if a is None or b is None:
        return None
    # amount in base = amount / a, then * b
    return (amount / a) * b


def list_supported() -> list[str]:
    """Return all known currency codes (sorted)."""
    return sorted(CURRENCY_NAMES.keys())
