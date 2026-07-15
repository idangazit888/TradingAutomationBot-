"""
Live-mode startup checks: USDC approval, credential management, open position recovery.

Called once before bot.run() when --live is used.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger("theSecondBot.live_startup")


# ──────────────────────────────────────────────────────────────────────────────
# #1  Authentication & credential refresh
# ──────────────────────────────────────────────────────────────────────────────

class CredentialManager:
    """
    Manages Polymarket API credentials.

    How POLY_1271 (signature_type=3) works
    ────────────────────────────────────────
    Polymarket uses EIP-1271 "contract-signature" verification.  Your EOA wallet
    (PRIVATE_KEY) signs a derived API key.  The CLOB verifies the signature via
    the Polymarket proxy contract on Polygon, which implements isValidSignature().

    The API key has a TTL (≈24 h).  When it expires every REST call returns 401.
    We catch that and re-derive automatically without restarting the bot.

    The deposit wallet (DEPOSIT_WALLET / funder) is the address that actually
    holds USDC.  It must match the wallet that funded via the Polymarket UI.
    """

    def __init__(self, private_key: str, deposit_addr: str, chain_id: int = 137):
        self.private_key = private_key
        self.deposit_addr = deposit_addr
        self.chain_id = chain_id
        self._client = None
        self._creds = None
        self._cred_ts: float = 0.0
        self._TTL = 23 * 3600  # refresh proactively at 23 h (before 24 h expiry)

    def get_client(self):
        """Return a live ClobClient, refreshing credentials if needed."""
        if self._client is None or self._creds_expired():
            self._build_client()
        return self._client

    def _creds_expired(self) -> bool:
        return time.time() - self._cred_ts >= self._TTL

    def _build_client(self):
        from py_clob_client_v2 import ClobClient, SignatureTypeV2
        from eth_account import Account
        import os
        host = "https://clob.polymarket.com"

        eoa_address = Account.from_key(self.private_key).address

        # PROXY_WALLET → Magic Link / Polymarket embedded wallet (EIP-1271 contract sig)
        proxy_wallet = os.getenv("PROXY_WALLET")
        if proxy_wallet:
            sig_type = SignatureTypeV2.POLY_1271
            funder = proxy_wallet
            logger.info(f"Wallet type: POLY_1271/Magic Link (sig_type={int(sig_type)}, "
                        f"funder={funder}, signer={eoa_address})")
        else:
            # Fall back: compare EOA vs DEPOSIT_WALLET to auto-detect
            is_proxy = eoa_address.lower() != self.deposit_addr.lower()
            if is_proxy:
                sig_type = SignatureTypeV2.POLY_PROXY
                funder = self.deposit_addr
            else:
                sig_type = SignatureTypeV2.EOA
                funder = None
            logger.info(f"Wallet type: {'POLY_PROXY' if is_proxy else 'EOA'} "
                        f"(sig_type={int(sig_type)}, funder={funder or eoa_address})")

        # Step 1: derive API key using the same sig_type + funder so the API key
        # is associated with the correct maker address.
        temp = ClobClient(
            host,
            key=self.private_key,
            chain_id=self.chain_id,
            signature_type=sig_type,
            funder=funder,
        )
        try:
            creds = temp.create_or_derive_api_key()
        except Exception as e:
            raise RuntimeError(f"Failed to derive API key: {e}") from e

        # Step 2: authenticated client with credentials
        self._client = ClobClient(
            host,
            key=self.private_key,
            chain_id=self.chain_id,
            creds=creds,
            signature_type=sig_type,
            funder=funder,
        )
        self._creds = creds
        self._cred_ts = time.time()
        logger.info("🔐 CLOB credentials (re)derived OK")

    def handle_auth_error(self):
        """Force credential refresh — call when a 401/403 is returned."""
        logger.warning("Auth error — forcing credential refresh")
        self._cred_ts = 0.0
        self._build_client()


# ──────────────────────────────────────────────────────────────────────────────
# #9  USDC allowance & approval
# ──────────────────────────────────────────────────────────────────────────────

def check_and_approve_usdc(client) -> bool:
    """
    Polymarket uses two contracts on Polygon that need USDC approval:
      1. CTF Exchange (ERC-1155 conditional token trading)
      2. Neg-Risk Adapter (handles the UP+DOWN binary structure)

    py_clob_client_v2 exposes:
      client.approve_usdc()   — sets max allowance on the CTF Exchange
      client.set_allowance()  — sets allowance for the Neg-Risk Adapter

    This only needs to happen once per wallet ever, but calling it when already
    approved is a no-op (the on-chain tx still costs ~0.001 MATIC though).

    We therefore check whether approval is already in place first.
    Gas: each approval tx costs ~60k gas ≈ 0.001 MATIC ≈ $0.001 at current prices.
    This is negligible and only happens once.
    """
    try:
        # Check current allowances
        allowances = _get_allowances(client)
        needs_exchange = allowances.get("exchange", 0) < 1e18
        needs_neg_risk = allowances.get("neg_risk", 0) < 1e18

        if not needs_exchange and not needs_neg_risk:
            logger.info("✅ USDC allowances already set")
            return True

        if needs_exchange:
            logger.info("Setting USDC allowance for CTF Exchange…")
            client.approve_usdc()
            logger.info("✅ CTF Exchange allowance set")

        if needs_neg_risk:
            logger.info("Setting USDC allowance for Neg-Risk Adapter…")
            # Some SDK versions expose set_allowance(contract_type="neg_risk")
            try:
                client.set_allowance()
                logger.info("✅ Neg-Risk allowance set")
            except Exception:
                # Older SDK — allowance included in approve_usdc
                pass

        return True

    except Exception as e:
        logger.error(f"USDC approval check failed: {e}")
        logger.warning("Proceeding anyway — approval may have been set previously")
        return False


def _get_allowances(client) -> dict:
    """Try to read on-chain allowances; return zeros if SDK doesn't support it."""
    result = {"exchange": 0, "neg_risk": 0}
    try:
        # Some SDK versions expose this
        if hasattr(client, "get_usdc_allowance"):
            result["exchange"] = client.get_usdc_allowance() or 0
        elif hasattr(client, "get_allowance"):
            result["exchange"] = client.get_allowance() or 0
        # Assume neg_risk is same as exchange when using the same approve call
        result["neg_risk"] = result["exchange"]
    except Exception:
        pass
    return result


# ──────────────────────────────────────────────────────────────────────────────
# #5  On-chain balance sync
# ──────────────────────────────────────────────────────────────────────────────

def fetch_onchain_balance(client) -> Optional[float]:
    """
    Fetch the real USDC collateral balance available for trading on Polymarket.

    The correct SDK method is get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)).
    The response is a dict with a "balance" key holding a decimal string in USDC units.

    Fallback chain for SDK version differences:
      1. get_balance_allowance with AssetType.COLLATERAL  (current SDK)
      2. get_balance()
      3. get_collateral_balance()
    """
    try:
        from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
        raw = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        # Response is a dict. "balance" may be either a human-readable decimal
        # string ("61.234567") or a raw micro-USDC integer (61234567).
        # USDC has 6 decimals, so divide by 1_000_000 when the value is >= 1000.
        if isinstance(raw, dict):
            val = raw.get("balance") or raw.get("collateral_balance") or 0
        else:
            val = raw
        result = float(val) if val is not None else None
        if result is not None:
            if result >= 1000:  # raw micro-USDC — convert to dollars
                result = result / 1_000_000
                logger.info(f"💰 on-chain USDC balance (converted from micro-USDC): ${result:.4f}")
            else:
                logger.info(f"💰 on-chain USDC balance: ${result:.4f}")
        return result
    except Exception:
        pass

    # Fallback — older SDK versions
    try:
        if hasattr(client, "get_balance"):
            raw = client.get_balance()
            return float(raw) if raw is not None else None
        if hasattr(client, "get_collateral_balance"):
            raw = client.get_collateral_balance()
            return float(raw) if raw is not None else None
    except Exception as e:
        logger.warning(f"Balance fetch failed: {type(e).__name__}: {e}")

    logger.warning("SDK has no working balance method — real P&L tracking disabled")
    return None


# ──────────────────────────────────────────────────────────────────────────────
# #10  Startup open position recovery
# ──────────────────────────────────────────────────────────────────────────────

def query_open_positions(client) -> list[dict]:
    """
    Query Polymarket CLOB REST for any positions our wallet currently holds.

    Why this matters
    ─────────────────
    If the bot crashes (power outage, VPS reboot) while a position is open, the
    position stays open on Polymarket.  On restart the bot has no memory of it.
    Without this check, the internal bankroll and the real balance diverge every
    time there's a crash.

    Returns a list of dicts:
      {
        "token_id": str,        # ERC-1155 token ID
        "market_id": str,       # condition ID (may be empty if API doesn't return it)
        "size": float,          # number of shares held
        "avg_price": float,     # average acquisition price (best effort)
        "outcome": str,         # "UP" / "DOWN" / unknown
      }
    """
    positions = []
    try:
        # py_clob_client_v2 may expose get_positions() or get_portfolio()
        raw = None
        if hasattr(client, "get_positions"):
            raw = client.get_positions()
        elif hasattr(client, "get_portfolio"):
            raw = client.get_portfolio()
        elif hasattr(client, "get_balances"):
            raw = client.get_balances()

        if raw is None:
            logger.warning("SDK has no positions method — cannot check for ghost positions")
            return []

        items = raw if isinstance(raw, list) else (raw.get("data") or raw.get("positions") or [])
        for item in items:
            if isinstance(item, dict):
                size = float(item.get("size") or item.get("balance") or 0)
            else:
                size = float(getattr(item, "size", 0) or getattr(item, "balance", 0))
            if size <= 0.0:
                continue
            token_id = str(item.get("asset_id") or item.get("token_id") or
                           getattr(item, "asset_id", "") or getattr(item, "token_id", ""))
            outcome = str(item.get("outcome") or getattr(item, "outcome", "unknown")).upper()
            avg_price = float(item.get("avg_price") or item.get("price") or
                              getattr(item, "avg_price", 0) or 0)
            positions.append({
                "token_id": token_id,
                "market_id": str(item.get("condition_id") or item.get("market") or
                                 getattr(item, "condition_id", "") or ""),
                "size": size,
                "avg_price": avg_price,
                "outcome": outcome,
            })
    except Exception as e:
        logger.warning(f"Position query failed: {e}")
    return positions


def query_live_orders(client) -> list[dict]:
    """Return any unfilled (LIVE) orders — needed to cancel orphan orders after crash."""
    orders = []
    try:
        raw = None
        if hasattr(client, "get_orders"):
            raw = client.get_orders()
        elif hasattr(client, "get_open_orders"):
            raw = client.get_open_orders()
        if raw is None:
            return []
        items = raw if isinstance(raw, list) else (raw.get("data") or [])
        for item in items:
            status = str(item.get("status") or getattr(item, "status", "")).upper()
            if status in ("LIVE", "OPEN", "ACTIVE"):
                orders.append({
                    "order_id": str(item.get("id") or item.get("order_id") or
                                   getattr(item, "id", "")),
                    "token_id": str(item.get("asset_id") or item.get("token_id") or
                                   getattr(item, "asset_id", "")),
                    "side": str(item.get("side") or getattr(item, "side", "")),
                    "size": float(item.get("original_size") or item.get("size") or 0),
                    "price": float(item.get("price") or 0),
                })
    except Exception as e:
        logger.warning(f"Live orders query failed: {e}")
    return orders


def cancel_all_live_orders(client) -> int:
    """Cancel any orphan orders left from a previous session crash."""
    orders = query_live_orders(client)
    cancelled = 0
    for o in orders:
        try:
            client.cancel_order(o["order_id"])
            cancelled += 1
            logger.info(f"Cancelled orphan order {o['order_id'][:8]}… ({o['side']} {o['size']} @ {o['price']})")
        except Exception as e:
            logger.warning(f"Failed to cancel order {o['order_id']}: {e}")
    if cancelled:
        logger.info(f"Cancelled {cancelled} orphan order(s) from previous session")
    return cancelled


# ──────────────────────────────────────────────────────────────────────────────
# Full pre-flight check — called from run.py
# ──────────────────────────────────────────────────────────────────────────────

def run_live_preflight(client, starting_bankroll: float) -> float:
    """
    Run all live-mode startup checks.  Returns the authoritative starting bankroll
    (from on-chain balance, not the CLI argument).

    Steps:
      1. USDC approval
      2. Cancel orphan orders from previous crash
      3. Query and log any open positions
      4. Fetch on-chain balance and use it as the real starting bankroll
    """
    logger.info("=== Live pre-flight checks ===")

    # 1. Approvals
    check_and_approve_usdc(client)

    # 2. Orphan orders
    cancel_all_live_orders(client)

    # 3. Open positions — log as warning; bot will reconcile via _startup_position_check
    open_positions = query_open_positions(client)
    if open_positions:
        logger.warning(f"⚠️  Found {len(open_positions)} open position(s) from previous session:")
        for p in open_positions:
            logger.warning(f"   {p['outcome']} token={p['token_id'][:12]}… size={p['size']:.2f} avg={p['avg_price']:.3f}")
    else:
        logger.info("✅ No open positions from previous session")

    # 4. Real balance — always authoritative; CLI --bankroll is only used for
    # mismatch warnings when the user explicitly passed a value (starting_bankroll > 0).
    real_balance = fetch_onchain_balance(client)
    if real_balance is not None:
        if starting_bankroll > 0:
            diff = real_balance - starting_bankroll
            if abs(diff) > 0.50:
                logger.warning(f"⚠️  Balance mismatch: CLI arg ${starting_bankroll:.2f} vs on-chain ${real_balance:.2f} (Δ${diff:+.2f})")
        logger.info(f"✅ Starting bankroll from Polymarket wallet: ${real_balance:.2f}")
        return real_balance
    else:
        fallback = starting_bankroll if starting_bankroll > 0 else 50.0
        logger.warning(f"Could not fetch on-chain balance — using ${fallback:.2f}")
        return fallback
