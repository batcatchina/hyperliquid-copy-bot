# /app/data/所有对话/主对话/hltrade_core.py

import time
import json
import math
import hashlib
import msgpack
from decimal import Decimal, ROUND_DOWN, getcontext

import requests

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak, to_hex


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


# ─────────────────────────────────────────────────────────────────────────────
# EIP-712 secp256k1 Signing (Hyperliquid real trading)
# ─────────────────────────────────────────────────────────────────────────────

def _float_to_wire(x):
    rounded = f"{float(x):.8f}"
    if abs(float(rounded) - float(x)) >= 1e-12:
        raise ValueError(f"float_to_wire rounding error for {x}")
    if rounded == "-0":
        rounded = "0"
    d = Decimal(rounded).normalize()
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s else "0"


def _order_type_to_wire(order_type):
    if isinstance(order_type, str):
        if order_type == "market":
            return {"market": {"tif": "Ioc"}}
        if order_type == "limit":
            return {"limit": {"tif": "Gtc"}}
    if isinstance(order_type, dict):
        if "limit" in order_type:
            return {"limit": {"tif": order_type["limit"].get("tif", "Gtc")}}
        if "market" in order_type:
            return {"market": {"tif": order_type["market"].get("tif", "Ioc")}}
    return {"limit": {"tif": "Gtc"}}


def _action_hash(action, vault_address, nonce):
    data = msgpack.packb(action)
    data += nonce.to_bytes(8, "big")
    if vault_address is None:
        data += b"\x00"
    else:
        data += b"\x01" + bytes.fromhex(vault_address.replace("0x", ""))
    return keccak(data)


def _phantom_agent(connection_id, is_mainnet):
    return {
        "source": "a" if is_mainnet else "b",
        "connectionId": connection_id,
    }


def _sign_l1_action(wallet, action, vault_address, nonce, is_mainnet):
    h = _action_hash(action, vault_address, nonce)
    phantom = _phantom_agent(h, is_mainnet)
    typed_data = {
        "domain": {
            "chainId": 1337,
            "name": "Exchange",
            "verifyingContract": "0x0000000000000000000000000000000000000000",
            "version": "1",
        },
        "types": {
            "Agent": [
                {"name": "source", "type": "string"},
                {"name": "connectionId", "type": "bytes32"},
            ],
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
        },
        "primaryType": "Agent",
        "message": phantom,
    }
    encoded = encode_typed_data(full_message=typed_data)
    signed = wallet.sign_message(encoded)
    return {
        "r": to_hex(signed.r.to_bytes(32, "big")),
        "s": to_hex(signed.s.to_bytes(32, "big")),
        "v": signed.v,
    }


def sign_order(order_dict, private_key):
    """
    Sign a Hyperliquid order using EIP-712 secp256k1.
    order_dict: {coin, is_buy, size, order_type, testnet, limit_px}
    Returns signed order dict for send_order().
    """
    is_mainnet = not order_dict.get("testnet", True)

    # Resolve asset index via /info meta
    meta_resp = _post_json(MAINNET_INFO_URL, {"type": "meta"})
    universe = meta_resp.get("universe", [])
    asset_index = None
    for i, item in enumerate(universe):
        if item.get("name") == order_dict["coin"]:
            asset_index = i
            break
    if asset_index is None:
        raise ValueError(f"Unknown coin: {order_dict['coin']}")

    limit_px = order_dict.get("limit_px") or order_dict.get("price") or 0
    order_wire = {
        "a": asset_index,
        "b": bool(order_dict["is_buy"]),
        "s": _float_to_wire(order_dict["size"]),
        "p": _float_to_wire(limit_px),
        "r": False,
        "t": _order_type_to_wire(order_dict.get("order_type", "market")),
    }

    action = {
        "type": "order",
        "orders": [order_wire],
        "grouping": "na",
    }

    wallet = Account.from_key(private_key)
    nonce = int(time.time() * 1000)
    signature = _sign_l1_action(wallet, action, None, nonce, is_mainnet)

    return {
        "action": action,
        "nonce": nonce,
        "signature": signature,
        "vaultAddress": None,
        "address": wallet.address,
        "testnet": order_dict.get("testnet", True),
    }


def send_order(signed_order, testnet=True):
    url = TESTNET_EXCHANGE_URL if testnet else MAINNET_EXCHANGE_URL
    payload = {
        "action": signed_order["action"],
        "nonce": signed_order["nonce"],
        "signature": signed_order["signature"],
    }
    if signed_order.get("vaultAddress"):
        payload["vaultAddress"] = signed_order["vaultAddress"]
    return _post_json(url, payload)


def get_all_positions(wallet, private_key):
    """
    Retrieve all positions for a wallet via the Hyperliquid info endpoint.

    Args:
        wallet:  Wallet address (0x...)
        private_key:  Private key hex string (0x...)  # kept for API parity, not used for signing here

    Returns:
        dict:  Raw API response — {"assetPositions": [...], "marginSummary": {...}}
              or the full clearinghouseState dict.
    Raises:
        RuntimeError:  On API error.
    """
    # clearinghouseState is the correct /info action for private position queries.
    # It does NOT require signing — it is an unsigned /info endpoint.
    # (The /exchange endpoint only accepts signed L1 trading actions: order/cancel/etc.)
    payload = {
        "type": "clearinghouseState",
        "user": wallet,
    }
    data = _post_json(MAINNET_INFO_URL, payload)
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Original functions (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def get_user_fills(wallet):
    payload = {"type": "userFills", "user": wallet}
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
    payload = {"type": "allMids"}
    data = _post_json(MAINNET_INFO_URL, payload)
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected allMids response: {data}")
    return data


def get_clearinghouse_state(wallet):
    """
    Retrieve clearinghouse state (positions + margin summary) for any wallet.
    Uses the public /info endpoint — NO signature required.

    Args:
        wallet: Wallet address (0x...)

    Returns:
        dict: {"marginSummary": {...}, "assetPositions": [...]} or empty dict.
    Raises:
        RuntimeError: On API error.
    """
    payload = {"type": "clearinghouseState", "user": wallet}
    return _post_json(MAINNET_INFO_URL, payload)


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
    if asset_price <= 0 or max_pos <= 0:
        raise ValueError("Asset price and max_position must be positive")
    max_size_by_notional = max_pos / asset_price
    final_size = min(copied_size, max_size_by_notional)
    final_size = _floor_to_step(final_size, Decimal("0.0001"))
    if final_size <= 0:
        return {
            "asset": asset, "side": side,
            "price": _format_decimal(fill_price),
            "size": "0", "notional": "0",
            "skipped": True, "reason": "size_below_min_or_max_position_zero",
        }
    notional = final_size * asset_price
    return {
        "asset": asset, "side": side,
        "price": _format_decimal(fill_price),
        "size": _format_decimal(final_size),
        "notional": _format_decimal(notional),
        "skipped": False, "tid": _extract_tid(fill),
    }
