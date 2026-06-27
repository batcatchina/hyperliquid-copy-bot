# /app/data/所有对话/主对话/hltrade_core.py

import time
import json
import math
import hashlib
from decimal import Decimal, ROUND_DOWN, getcontext

import requests

try:
    from nacl.signing import SigningKey
except Exception:
    SigningKey = None


getcontext().prec = 28

MAINNET_INFO_URL = "https://api.hyperliquid.xyz/info"
MAINNET_EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange"
TESTNET_EXCHANGE_URL = "https://api.hyperliquid-testnet.xyz/exchange"

DEFAULT_TIMEOUT = 15
HTTP_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "hltrade-core/1.0",
}

_seen_fill_tids = set()


def _post_json(url, payload, timeout=DEFAULT_TIMEOUT):
    resp = requests.post(url, headers=HTTP_HEADERS, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("status") == "err":
        raise RuntimeError(f"Hyperliquid error: {data}")
    return data


def _normalize_hex_key(private_key):
    if not isinstance(private_key, str):
        raise TypeError("private_key must be a hex string")
    key = private_key.lower().strip()
    if key.startswith("0x"):
        key = key[2:]
    if len(key) != 64:
        raise ValueError("private_key must be a 32-byte hex string")
    return bytes.fromhex(key)


def _canonical_json(obj):
    return json.dumps(obj, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _decimal_places(value):
    s = format(Decimal(str(value)).normalize(), "f")
    if "." not in s:
        return 0
    return len(s.rstrip("0").split(".")[1]) if "." in s else 0


def _floor_to_step(value, step):
    v = Decimal(str(value))
    s = Decimal(str(step))
    if s <= 0:
        return v
    return (v / s).to_integral_value(rounding=ROUND_DOWN) * s


def _format_decimal(d, max_decimals=8):
    q = Decimal("1." + ("0" * max_decimals))
    x = Decimal(d).quantize(q, rounding=ROUND_DOWN)
    s = format(x.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def _infer_asset(fill):
    for key in ("coin", "asset", "symbol"):
        if key in fill and fill[key]:
            return fill[key]
    return None


def _infer_side(fill):
    side = str(fill.get("side", "")).lower()
    if side in ("b", "buy", "long", "bid"):
        return "buy"
    if side in ("s", "sell", "short", "ask"):
        return "sell"

    sz = fill.get("sz")
    if sz is not None:
        try:
            return "buy" if Decimal(str(sz)) > 0 else "sell"
        except Exception:
            pass
    return "buy"


def _infer_fill_size(fill):
    for key in ("sz", "size", "pxSz"):
        if key in fill:
            try:
                return abs(Decimal(str(fill[key])))
            except Exception:
                continue
    return Decimal("0")


def _infer_fill_price(fill, price_map):
    for key in ("px", "price"):
        if key in fill:
            try:
                return Decimal(str(fill[key]))
            except Exception:
                pass
    asset = _infer_asset(fill)
    if asset and asset in price_map:
        return Decimal(str(price_map[asset]))
    raise ValueError("Unable to determine fill price")


def _extract_tid(fill):
    for key in ("tid", "tradeId", "id"):
        if key in fill:
            return str(fill[key])
    return None


def get_user_fills(wallet):
    payload = {
        "type": "userFills",
        "user": wallet,
    }
    data = _post_json(MAINNET_INFO_URL, payload)
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected userFills response: {data}")

    out = []
    for fill in data:
        tid = _extract_tid(fill)
        if tid is None:
            out.append(fill)
            continue
        if tid in _seen_fill_tids:
            continue
        _seen_fill_tids.add(tid)
        out.append(fill)
    return out


def get_all_mids():
    payload = {
        "type": "allMids",
    }
    data = _post_json(MAINNET_INFO_URL, payload)
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected allMids response: {data}")
    return data


def sign_order(order_dict, private_key):
    if SigningKey is None:
        raise ImportError("PyNaCl is required for Ed25519 signing: pip install pynacl")

    key_bytes = _normalize_hex_key(private_key)
    signing_key = SigningKey(key_bytes)

    order_payload = dict(order_dict)
    order_payload_str = _canonical_json(order_payload)
    order_hash = hashlib.sha256(order_payload_str.encode("utf-8")).digest()
    signed = signing_key.sign(order_hash)

    return {
        "signature": signed.signature.hex(),
        "publicKey": signing_key.verify_key.encode().hex(),
        "payloadHash": order_hash.hex(),
        "scheme": "ed25519",
    }


def send_order(order_dict, signature, testnet=True):
    url = TESTNET_EXCHANGE_URL if testnet else MAINNET_EXCHANGE_URL
    payload = {
        "action": order_dict,
        "nonce": order_dict.get("nonce", int(time.time() * 1000)),
        "signature": signature,
    }
    return _post_json(url, payload)


def calc_size(fill, price_map, copy_ratio, max_position):
    asset = _infer_asset(fill)
    if not asset:
        raise ValueError("Unable to determine fill asset")

    side = _infer_side(fill)
    fill_size = _infer_fill_size(fill)
    fill_price = _infer_fill_price(fill, price_map)

    if fill_size <= 0:
        raise ValueError("Fill size must be positive")
    if fill_price <= 0:
        raise ValueError("Price must be positive")

    copied_size = fill_size * Decimal(str(copy_ratio))
    asset_price = Decimal(str(price_map.get(asset, fill_price)))
    max_pos = Decimal(str(max_position))

    if asset_price <= 0:
        raise ValueError("Asset price must be positive")
    if max_pos <= 0:
        raise ValueError("max_position must be positive")

    max_size_by_notional = max_pos / asset_price
    final_size = min(copied_size, max_size_by_notional)
    final_size = _floor_to_step(final_size, Decimal("0.0001"))

    if final_size <= 0:
        return {
            "asset": asset,
            "side": side,
            "price": _format_decimal(fill_price),
            "size": "0",
            "notional": "0",
            "skipped": True,
            "reason": "size_below_min_or_max_position_zero",
        }

    notional = final_size * asset_price

    return {
        "asset": asset,
        "side": side,
        "price": _format_decimal(fill_price),
        "size": _format_decimal(final_size),
        "notional": _format_decimal(notional),
        "skipped": False,
        "tid": _extract_tid(fill),
    }