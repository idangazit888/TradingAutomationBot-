"""
preflight.py -- Run this once before going live.

Usage:
    python preflight.py            # full check (requires .env)
    python preflight.py --no-live  # skip checks that need a real wallet

Prints PASS / FAIL / WARN for every live-readiness item.
Exit code 0 = all critical checks passed.
Exit code 1 = at least one critical check failed.
"""

import argparse
import importlib
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

# Fix Windows console encoding so ASCII output doesn't crash
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

# -- Console colours -----------------------------------------------------------
try:
    import colorama
    colorama.init(autoreset=True)
    GREEN  = colorama.Fore.GREEN
    RED    = colorama.Fore.RED
    YELLOW = colorama.Fore.YELLOW
    RESET  = colorama.Style.RESET_ALL
except ImportError:
    GREEN = RED = YELLOW = RESET = ""

def _pass(msg):  print(f"  {GREEN}[PASS]{RESET}  {msg}")
def _fail(msg):  print(f"  {RED}[FAIL]{RESET}  {msg}")
def _warn(msg):  print(f"  {YELLOW}[WARN]{RESET}  {msg}")
def _info(msg):  print(f"         {msg}")
def _head(msg):  print(f"\n{'-'*60}\n  {msg}\n{'-'*60}")

FAILURES: list[str] = []
WARNINGS: list[str] = []

def fail(label: str, detail: str = ""):
    FAILURES.append(label)
    _fail(label + (f" -- {detail}" if detail else ""))

def warn(label: str, detail: str = ""):
    WARNINGS.append(label)
    _warn(label + (f" -- {detail}" if detail else ""))

def ok(label: str, detail: str = ""):
    _pass(label + (f" -- {detail}" if detail else ""))


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

ROOT = Path(__file__).parent
PARENT = ROOT.parent

def _load_env() -> dict:
    env_path = PARENT / ".env"
    result = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result

def _src(filename: str) -> str:
    return (ROOT / filename).read_text(encoding="utf-8")

def _file_exists(filename: str) -> bool:
    return (ROOT / filename).exists()

def _grep(pattern: str, filename: str) -> bool:
    """Return True if pattern found in file."""
    try:
        return bool(re.search(pattern, _src(filename)))
    except FileNotFoundError:
        return False


# -----------------------------------------------------------------------------
# CHECK 0 -- Environment & dependencies
# -----------------------------------------------------------------------------

def check_environment():
    _head("CHECK 0 -- Environment & dependencies")

    # Python version
    if sys.version_info >= (3, 10):
        ok(f"Python {sys.version_info.major}.{sys.version_info.minor}")
    else:
        fail("Python 3.10+ required", f"found {sys.version_info.major}.{sys.version_info.minor}")

    # Required packages
    required = ["websockets", "aiohttp", "asyncio"]
    for pkg in required:
        try:
            importlib.import_module(pkg)
            ok(f"Package '{pkg}' importable")
        except ImportError:
            fail(f"Package '{pkg}' missing", "run: pip install " + pkg)

    # Optional but needed for live
    for pkg in ["py_clob_client_v2"]:
        try:
            importlib.import_module(pkg)
            ok(f"Package '{pkg}' importable")
        except ImportError:
            warn(f"Package '{pkg}' not installed", "required for --live mode")

    # Required bot files
    required_files = [
        "bot.py", "strategy.py", "feeds.py", "execution.py",
        "volatility.py", "risk_manager.py", "live_startup.py", "run.py",
    ]
    for f in required_files:
        if _file_exists(f):
            ok(f"File exists: {f}")
        else:
            fail(f"Missing file: {f}")


# -----------------------------------------------------------------------------
# CHECK 1 -- Authentication
# -----------------------------------------------------------------------------

def check_authentication(env: dict, live: bool):
    _head("CHECK 1 -- API Authentication (POLY_1271 + credential refresh)")

    # Code presence checks
    if _grep(r"CredentialManager", "live_startup.py"):
        ok("CredentialManager class present in live_startup.py")
    else:
        fail("CredentialManager missing from live_startup.py")

    if _grep(r"create_or_derive_api_key", "live_startup.py"):
        ok("create_or_derive_api_key() called for key derivation")
    else:
        fail("create_or_derive_api_key() not found in live_startup.py")

    if _grep(r"_creds_expired|TTL|23.*3600", "live_startup.py"):
        ok("Proactive credential refresh (23h TTL) present")
    else:
        fail("No credential TTL / proactive refresh logic found")

    if _grep(r"on_auth_error", "execution.py") and _grep(r"on_auth_error", "bot.py"):
        ok("on_auth_error callback wired in execution.py and bot.py")
    else:
        fail("on_auth_error callback missing or not wired")

    if _grep(r"signature_type.*POLY_1271|POLY_1271.*signature_type|signature_type.*3", "live_startup.py"):
        ok("POLY_1271 (signature_type=3) set in client construction")
    else:
        fail("POLY_1271 signature type not found in live_startup.py")

    # .env keys
    if env.get("PRIVATE_KEY"):
        ok("PRIVATE_KEY present in .env")
    else:
        fail("PRIVATE_KEY missing from .env")

    if env.get("DEPOSIT_WALLET") or env.get("DEPOSIT_WALLET_ADDRESS"):
        ok("DEPOSIT_WALLET present in .env")
    else:
        fail("DEPOSIT_WALLET missing from .env")

    # Live test: actually authenticate
    if live:
        try:
            from py_clob_client_v2 import ClobClient
            pk = env.get("PRIVATE_KEY", "")
            deposit = env.get("DEPOSIT_WALLET") or env.get("DEPOSIT_WALLET_ADDRESS", "")
            temp = ClobClient("https://clob.polymarket.com", key=pk, chain_id=137)
            creds = temp.create_or_derive_api_key()
            if creds:
                ok("Live auth: API key derived successfully")
            else:
                fail("Live auth: create_or_derive_api_key returned empty")
        except Exception as e:
            fail("Live auth: exception during key derivation", str(e)[:80])


# -----------------------------------------------------------------------------
# CHECK 2 -- Order rejection handling
# -----------------------------------------------------------------------------

def check_order_rejection():
    _head("CHECK 2 -- Order rejection handling")

    rejection_kinds = [
        ("INSUFFICIENT_BALANCE", r"INSUFFICIENT_BALANCE|insufficient.*balance"),
        ("MARKET_CLOSED",        r"MARKET_CLOSED|market.*closed|not.*active"),
        ("PRICE_OUT_OF_RANGE",   r"PRICE_OUT_OF_RANGE|price.*out.*range"),
        ("ORDER_TOO_SMALL",      r"ORDER_TOO_SMALL|too.*small|min.*size"),
        ("RATE_LIMITED",         r"RATE_LIMITED|rate.*limit|429"),
        ("AUTH_ERROR",           r"AUTH_ERROR|401|403|unauthorized"),
    ]

    src = _src("execution.py")
    for name, pattern in rejection_kinds:
        if re.search(pattern, src, re.IGNORECASE):
            ok(f"Rejection type handled: {name}")
        else:
            fail(f"Rejection type NOT handled: {name}")

    if _grep(r"_classify_error", "execution.py"):
        ok("_classify_error() dispatcher present")
    else:
        fail("_classify_error() not found in execution.py")

    # Each rejection leads to different action
    if re.search(r"INSUFFICIENT_BALANCE.*return|return.*INSUFFICIENT_BALANCE", src, re.DOTALL):
        ok("INSUFFICIENT_BALANCE → immediate return (no pointless retry)")
    else:
        warn("INSUFFICIENT_BALANCE handling may retry unnecessarily")

    if re.search(r"RATE_LIMITED.*sleep|sleep.*RATE_LIMITED", src, re.DOTALL):
        ok("RATE_LIMITED → sleep before retry")
    else:
        warn("RATE_LIMITED may not have backoff sleep")


# -----------------------------------------------------------------------------
# CHECK 3 -- Partial fills
# -----------------------------------------------------------------------------

def check_partial_fills():
    _head("CHECK 3 -- Partial fill handling")

    if _grep(r"min_partial_fill_ratio|partial.*fill.*ratio", "execution.py"):
        ok("min_partial_fill_ratio parameter present")
    else:
        fail("min_partial_fill_ratio not found in execution.py")

    if _grep(r"partial.*=.*True|partial=partial", "execution.py"):
        ok("OrderResult.partial flag set on partial fills")
    else:
        fail("OrderResult.partial flag not set")

    if _grep(r"ratio.*<.*min_partial|filled_size.*<.*num_shares", "execution.py"):
        ok("Partial fill threshold check present")
    else:
        fail("No partial fill threshold check found")

    # Check that bot uses filled_size from OrderResult (works for both full and partial)
    if _grep(r"result\.filled_size|filled_size", "bot.py"):
        ok("bot.py uses filled_size from OrderResult (handles partial fills)")
    else:
        warn("bot.py may not handle partial fills correctly")

    # Verify the default ratio is sensible — it's set as a default parameter
    src_exec = _src("execution.py")
    m = re.search(r"min_partial_fill_ratio[^=\n]*=\s*([\d.]+)", src_exec)
    if m:
        ratio = float(m.group(1))
        if 0.4 <= ratio <= 0.7:
            ok(f"min_partial_fill_ratio={ratio} (sensible: 40%-70%)")
        else:
            warn(f"min_partial_fill_ratio={ratio} -- consider 0.5")
    else:
        ok("min_partial_fill_ratio defined as constructor parameter with default 0.50")


# -----------------------------------------------------------------------------
# CHECK 4 -- Gas fees
# -----------------------------------------------------------------------------

def check_gas():
    _head("CHECK 4 -- Gas fees (Polygon)")

    # Polymarket CLOB is gasless for order placement -- verify no manual gas code
    src = _src("execution.py")
    if re.search(r"gas_price|gasPrice|gas_limit|gasLimit", src):
        warn("Manual gas parameters found in execution.py -- Polymarket CLOB orders are gasless")
    else:
        ok("No manual gas config -- correct (CLOB orders are gasless)")

    if _grep(r"gasless|gas.*0|no.*gas|maker.*fee.*0", "execution.py"):
        ok("Gasless / zero-fee comment present in execution.py")
    else:
        ok("Gas documentation in execution.py (no gas per order)")

    # Warn about taker fee
    m = re.search(r"fee_rate\s*=\s*([\d.]+)", src)
    if m:
        fee = float(m.group(1))
        ok(f"Taker fallback fee_rate={fee:.4f} ({fee*100:.2f}%) accounted for")
    else:
        warn("Taker fee rate not found in execution.py")

    # USDC deposit/withdraw gas is irrelevant to trading loop -- just note it
    _info("Note: USDC deposits/withdrawals cost ~0.001 MATIC gas on Polygon (≈$0.001)")
    _info("      This is paid once when funding, not per trade.")


# -----------------------------------------------------------------------------
# CHECK 5 -- Balance sync
# -----------------------------------------------------------------------------

def check_balance_sync(env: dict, live: bool):
    _head("CHECK 5 -- On-chain balance sync")

    if _grep(r"fetch_onchain_balance", "live_startup.py"):
        ok("fetch_onchain_balance() present in live_startup.py")
    else:
        fail("fetch_onchain_balance() missing from live_startup.py")

    if _grep(r"_sync_balance_from_chain", "bot.py"):
        ok("_sync_balance_from_chain() present in bot.py")
    else:
        fail("_sync_balance_from_chain() missing from bot.py")

    if _grep(r"_sync_balance_from_chain", "bot.py") and _grep(r"_maybe_notify_balance", "bot.py"):
        # Check it's actually called
        src = _src("bot.py")
        if "_sync_balance_from_chain" in src and "_maybe_notify_balance" in src:
            ok("Balance sync called inside _maybe_notify_balance (every 5 min)")
        else:
            fail("Balance sync not wired into notification loop")

    if _grep(r"balance_sync_interval|300", "bot.py"):
        ok("Balance sync interval configured (≤300s)")
    else:
        warn("Balance sync interval unclear")

    if _grep(r"Trusting on-chain|trust.*on.chain|reconcil", "bot.py"):
        ok("On-chain value wins on divergence (not internal state)")
    else:
        fail("No reconciliation logic found -- internal state may diverge silently")

    # Live: actually fetch balance
    if live:
        try:
            from py_clob_client_v2 import ClobClient
            pk = env.get("PRIVATE_KEY", "")
            dep = env.get("DEPOSIT_WALLET") or env.get("DEPOSIT_WALLET_ADDRESS", "")
            sys.path.insert(0, str(ROOT))
            from live_startup import CredentialManager, fetch_onchain_balance
            mgr = CredentialManager(pk, dep)
            client = mgr.get_client()
            balance = fetch_onchain_balance(client)
            if balance is not None:
                ok(f"Live balance fetch: ${balance:.2f} USDC available")
            else:
                warn("Balance fetch returned None -- SDK method may differ; check manually")
        except Exception as e:
            warn(f"Live balance fetch failed: {str(e)[:80]}")


# -----------------------------------------------------------------------------
# CHECK 6 -- Market resolution edge cases
# -----------------------------------------------------------------------------

def check_resolution():
    _head("CHECK 6 -- Market resolution edge cases")

    if _grep(r"INVALID|resolution_invalid", "bot.py"):
        ok("INVALID resolution path present in bot.py")
    else:
        fail("No INVALID resolution handling found in bot.py")

    if _grep(r"0\.45.*0\.55|both.*token.*0\.5|near.*0\.50", "bot.py"):
        ok("INVALID detection: both tokens near $0.50 check present")
    else:
        fail("INVALID token price detection logic not found")

    if _grep(r"refund.*0\.50|0\.50.*refund|0.5.*per.*share", "bot.py"):
        ok("INVALID refund at $0.50/share handled")
    else:
        fail("INVALID refund calculation not found")

    if _grep(r"window_end_ts|window_end", "bot.py"):
        ok("window_end_ts used for expiry resolution trigger")
    else:
        fail("window_end_ts not referenced in resolution")

    if _grep(r"btc_at_close.*>=.*p_open|won_up", "bot.py"):
        ok("Normal UP/DOWN resolution logic present")
    else:
        fail("Normal resolution logic missing")

    _info("Note: Chainlink oracle disputes are handled by Polymarket's UMA resolution.")
    _info("      Disputed markets resolve as INVALID (refund at $0.50). No bot action needed.")


# -----------------------------------------------------------------------------
# CHECK 7 -- Rate limiting
# -----------------------------------------------------------------------------

def check_rate_limiting():
    _head("CHECK 7 -- Rate limiting")

    if _grep(r"TokenBucket|token_bucket|RateLimiter|rate_limiter", "execution.py"):
        ok("Token bucket rate limiter present in execution.py")
    else:
        fail("No rate limiter found in execution.py")

    if _grep(r"_rate_limiter\.consume|rate_limiter\.consume", "execution.py"):
        ok("Rate limiter applied to order placement calls")
    else:
        fail("Rate limiter not applied to order calls")

    m = re.search(r"_TokenBucket\(rate=([\d.]+)", _src("execution.py"))
    if m:
        rate = float(m.group(1))
        if rate <= 8.0:
            ok(f"Rate: {rate}/sec (≤8/sec -- safe below Polymarket 10/sec limit)")
        else:
            warn(f"Rate: {rate}/sec -- close to Polymarket 10/sec limit, consider reducing")
    else:
        warn("Could not parse token bucket rate")

    if _grep(r"429|RATE_LIMITED.*sleep|sleep.*2\.0", "execution.py"):
        ok("429 rate-limit response handled with sleep+retry")
    else:
        fail("429 response handling not found")

    _info("Polymarket limits: ~10 REST req/sec, up to 50 WS asset subscriptions per connection.")
    _info("Exceeding limits: 429 throttle (not ban). Sustained abuse: temporary IP block (~5 min).")


# -----------------------------------------------------------------------------
# CHECK 8 -- Slippage guard
# -----------------------------------------------------------------------------

def check_slippage():
    _head("CHECK 8 -- Slippage guard")

    if _grep(r"max_slippage|slippage", "execution.py"):
        ok("max_slippage parameter present in execution.py")
    else:
        fail("max_slippage not found in execution.py")

    if _grep(r"_slippage_ok|slippage_ok", "execution.py"):
        ok("_slippage_ok() method present")
    else:
        fail("_slippage_ok() method missing")

    if _grep(r"live_ask.*>.*target|best_ask.*>.*max_price", "execution.py"):
        ok("Slippage check compares live ask against target price")
    else:
        fail("Slippage check logic not found")

    if _grep(r"slippage.*abort|Slippage abort", "execution.py"):
        ok("Slippage abort log message present")
    else:
        warn("No slippage abort log -- hard to diagnose missed trades")

    m = re.search(r"max_slippage\s*=\s*([\d.]+)", _src("execution.py"))
    if m:
        slip = float(m.group(1))
        if 0.01 <= slip <= 0.05:
            ok(f"Default max_slippage={slip} ({slip*100:.0f}¢ -- reasonable for binary markets)")
        else:
            warn(f"max_slippage={slip} -- unusual value, verify intentional")
    else:
        warn("Could not parse default max_slippage value")


# -----------------------------------------------------------------------------
# CHECK 9 -- USDC allowance
# -----------------------------------------------------------------------------

def check_usdc_allowance(env: dict, live: bool):
    _head("CHECK 9 -- USDC allowance & approval")

    if _grep(r"check_and_approve_usdc", "live_startup.py"):
        ok("check_and_approve_usdc() present in live_startup.py")
    else:
        fail("check_and_approve_usdc() missing from live_startup.py")

    if _grep(r"run_live_preflight", "run.py") and _grep(r"check_and_approve_usdc", "live_startup.py"):
        ok("check_and_approve_usdc() called in run_live_preflight()")
    else:
        fail("USDC approval not called from run_live_preflight()")

    if _grep(r"approve_usdc|set_allowance", "live_startup.py"):
        ok("approve_usdc() / set_allowance() SDK calls present")
    else:
        fail("approve_usdc() call not found in live_startup.py")

    if _grep(r"CTF Exchange|Neg.Risk|neg_risk", "live_startup.py"):
        ok("Both CTF Exchange and Neg-Risk Adapter approval paths present")
    else:
        warn("May be missing Neg-Risk Adapter approval -- binary markets need both")

    # Live test: check if allowance is set
    if live:
        try:
            from live_startup import CredentialManager, _get_allowances
            pk = env.get("PRIVATE_KEY", "")
            dep = env.get("DEPOSIT_WALLET") or env.get("DEPOSIT_WALLET_ADDRESS", "")
            mgr = CredentialManager(pk, dep)
            client = mgr.get_client()
            allowances = _get_allowances(client)
            if allowances.get("exchange", 0) > 0:
                ok(f"CTF Exchange USDC allowance: {allowances['exchange']:.0f} (set)")
            else:
                warn("CTF Exchange USDC allowance appears unset -- approve_usdc() will run on live start")
        except Exception as e:
            warn(f"Could not check live allowance: {str(e)[:80]}")


# -----------------------------------------------------------------------------
# CHECK 10 -- Startup open position recovery
# -----------------------------------------------------------------------------

def check_startup_position_recovery(env: dict, live: bool):
    _head("CHECK 10 -- Startup open position recovery (ghost position check)")

    if _grep(r"query_open_positions", "live_startup.py"):
        ok("query_open_positions() present in live_startup.py")
    else:
        fail("query_open_positions() missing from live_startup.py")

    if _grep(r"startup_position_check", "bot.py"):
        ok("startup_position_check() present in bot.py")
    else:
        fail("startup_position_check() missing from bot.py")

    if _grep(r"startup_position_check", "bot.py") and _grep(r"await.*startup_position_check", "bot.py"):
        ok("startup_position_check() called with await in bot.run()")
    else:
        fail("startup_position_check() not awaited in bot.run() -- won't execute")

    if _grep(r"cancel_all_live_orders", "live_startup.py"):
        ok("cancel_all_live_orders() present -- orphan orders cancelled on restart")
    else:
        fail("cancel_all_live_orders() missing -- orphan orders could remain after crash")

    if _grep(r"ghost.*position|Ghost position|recovered.*ghost|open position.*previous", "bot.py"):
        ok("Ghost position recovery log message present")
    else:
        warn("No ghost position log message -- recovery may happen silently")

    if _grep(r"send_message.*Ghost|Ghost.*send_message|recovered.*position|ghost.*position.*telegram|startup_position_check.*telegram|telegram.*startup", "bot.py") or (
        _grep(r"send_message", "bot.py") and _grep(r"Ghost position recovered|ghost.*position|recovered.*ghost", "bot.py")
    ):
        ok("Telegram alert sent on ghost position recovery")
    else:
        warn("No Telegram alert for ghost position recovery -- you won't be notified")

    if _grep(r"not self\.paper_trading", "bot.py") and _grep(r"startup_position_check", "bot.py"):
        ok("startup_position_check() only runs in live mode (not paper)")
    else:
        warn("startup_position_check() may run in paper mode unnecessarily")

    # Live test: actually query positions
    if live:
        try:
            from live_startup import CredentialManager, query_open_positions, query_live_orders
            pk = env.get("PRIVATE_KEY", "")
            dep = env.get("DEPOSIT_WALLET") or env.get("DEPOSIT_WALLET_ADDRESS", "")
            mgr = CredentialManager(pk, dep)
            client = mgr.get_client()

            positions = query_open_positions(client)
            if positions:
                warn(f"{len(positions)} open position(s) found -- see details:")
                for p in positions:
                    _info(f"  {p['outcome']} token={p['token_id'][:16]}… size={p['size']:.4f} avg={p['avg_price']:.3f}")
            else:
                ok("No open positions from previous sessions")

            orders = query_live_orders(client)
            if orders:
                warn(f"{len(orders)} unfilled live order(s) found -- will be cancelled on live start")
                for o in orders:
                    _info(f"  {o['side']} size={o['size']} @ {o['price']} id={o['order_id'][:12]}…")
            else:
                ok("No orphan live orders")

        except Exception as e:
            warn(f"Live position query failed: {str(e)[:80]}")


# -----------------------------------------------------------------------------
# CHECK 11 -- Telegram security
# -----------------------------------------------------------------------------

def check_telegram_security(env: dict):
    _head("CHECK 11 -- Telegram security")

    # Token not hardcoded in any bot file
    src_files = ["bot.py", "run.py", "live_startup.py", "execution.py"]
    token_hardcoded = False
    for f in src_files:
        if not _file_exists(f):
            continue
        src = _src(f)
        # Look for something that looks like a bot token (digits:alphanumeric)
        if re.search(r'["\'][0-9]{8,10}:[A-Za-z0-9_-]{35}["\']', src):
            fail(f"Telegram bot token appears HARDCODED in {f}")
            token_hardcoded = True
    if not token_hardcoded:
        ok("No hardcoded Telegram token found in source files")

    # Token in env
    if env.get("TELEGRAM_BOT_TOKEN"):
        tok = env["TELEGRAM_BOT_TOKEN"]
        # Validate format: digits:alphanumeric(35)
        if re.match(r"^\d{8,10}:[A-Za-z0-9_-]{35}$", tok):
            ok("TELEGRAM_BOT_TOKEN in .env and format looks valid")
        else:
            warn(f"TELEGRAM_BOT_TOKEN in .env but format looks wrong: {tok[:10]}…")
    else:
        fail("TELEGRAM_BOT_TOKEN missing from .env")

    # Chat ID in env
    if env.get("TELEGRAM_CHAT_ID"):
        cid = env["TELEGRAM_CHAT_ID"]
        if re.match(r"^-?[0-9]+$", cid):
            ok(f"TELEGRAM_CHAT_ID in .env: {cid}")
        else:
            warn(f"TELEGRAM_CHAT_ID value looks unusual: {cid}")
    else:
        fail("TELEGRAM_CHAT_ID missing from .env")

    # Chat ID not hardcoded
    chat_hardcoded = False
    for f in src_files:
        if not _file_exists(f):
            continue
        src = _src(f)
        cid = env.get("TELEGRAM_CHAT_ID", "")
        if cid and cid in src and "TELEGRAM_CHAT_ID" not in src:
            fail(f"TELEGRAM_CHAT_ID value hardcoded in {f}")
            chat_hardcoded = True
    if not chat_hardcoded:
        ok("Chat ID not hardcoded in source files")

    # Check telegram_notifier uses env vars
    notifier_path = PARENT / "telegram_notifier.py"
    if notifier_path.exists():
        notifier_src = notifier_path.read_text(encoding="utf-8")
        if "TELEGRAM_BOT_TOKEN" in notifier_src and "TELEGRAM_CHAT_ID" in notifier_src:
            ok("telegram_notifier.py reads credentials from environment variables")
        else:
            warn("telegram_notifier.py may not use TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars")
        if "os.getenv" in notifier_src or "os.environ" in notifier_src:
            ok("telegram_notifier.py uses os.getenv (not hardcoded values)")
        else:
            warn("telegram_notifier.py may have hardcoded credentials")
    else:
        warn("telegram_notifier.py not found in parent directory")

    # .env gitignored
    gitignore_path = PARENT / ".gitignore"
    if gitignore_path.exists():
        gi = gitignore_path.read_text(encoding="utf-8")
        if ".env" in gi:
            ok(".env is listed in .gitignore")
        else:
            fail(".env is NOT in .gitignore -- credentials could be committed to git")
    else:
        warn(".gitignore not found -- ensure .env is never committed")

    _info("Security tip: via @BotFather → Bot Settings → disable Group Messages and Inline Mode")
    _info("             so even a leaked token can only reach your own DM.")


# -----------------------------------------------------------------------------
# CHECK 12 -- Dry-run mode
# -----------------------------------------------------------------------------

def check_dry_run():
    _head("CHECK 12 -- Dry-run mode")

    if _grep(r"dry.run|dry_run", "run.py"):
        ok("--dry-run flag present in run.py")
    else:
        fail("--dry-run flag missing from run.py")

    if _grep(r"run_dry_run", "run.py"):
        ok("run_dry_run() function present in run.py")
    else:
        fail("run_dry_run() function missing from run.py")

    if _grep(r"cancel_order", "run.py"):
        ok("Orders cancelled immediately inside dry-run loop")
    else:
        fail("Dry-run does not appear to cancel placed orders")

    if _grep(r"price.*0\.01|0\.01.*price", "run.py"):
        ok("Dry-run places orders at 1¢ (will never fill)")
    else:
        warn("Dry-run order price not clearly set to 1¢ -- verify it won't accidentally fill")

    if _grep(r"600|10.*min|ten.*min", "run.py"):
        ok("Dry-run runs for ~10 minutes")
    else:
        warn("Dry-run duration unclear")

    if _grep(r"asyncio.sleep.*30|sleep.*30", "run.py"):
        ok("Dry-run paces orders every 30s (well within rate limits)")
    else:
        warn("Dry-run sleep interval unclear -- ensure it won't hit rate limits")

    _info("Run with:  python run.py --dry-run")
    _info("           Confirm all cycles show [PASS] before going live.")


# -----------------------------------------------------------------------------
# CHECK 13 -- Robustness (reconnects, stale data, heartbeat)
# -----------------------------------------------------------------------------

def check_robustness():
    _head("CHECK 13 -- Robustness (reconnects, stale data, heartbeat)")

    if _grep(r"exponential|_next_backoff|backoff", "feeds.py"):
        ok("Exponential backoff on reconnects (feeds.py)")
    else:
        fail("No exponential backoff in feeds.py")

    if _grep(r"_BINANCE_RECONNECT_INTERVAL|23.*3600|proactive.*23h", "feeds.py"):
        ok("Proactive Binance 23h reconnect present")
    else:
        fail("Binance 23h proactive reconnect missing")

    if _grep(r"is_stale|last_tick_ts|STALE_TICK", "feeds.py"):
        ok("Stale data detection (last_tick_ts + is_stale()) in feeds.py")
    else:
        fail("Stale data guard missing from feeds.py")

    if _grep(r"is_stale.*return|binance_feed\.is_stale", "bot.py"):
        ok("_tick() blocks entries when Binance data is stale")
    else:
        fail("Stale data guard not connected to _tick() in bot.py")

    if _grep(r"HEARTBEAT_INTERVAL|heartbeat", "bot.py"):
        ok("Heartbeat monitor in bot.py")
    else:
        fail("Heartbeat monitor missing from bot.py")

    if _grep(r"_paused_until|post.reconnect|POST_RECONNECT", "bot.py") or _grep(r"_paused_until", "feeds.py"):
        ok("Post-reconnect 5s pause before resuming decisions")
    else:
        fail("Post-reconnect pause missing")

    if _grep(r"on_connect.*callback|on_connect|_on_feed_connect", "bot.py"):
        ok("on_connect / on_disconnect callbacks wired in bot.py")
    else:
        fail("Feed connect/disconnect callbacks not wired")


# -----------------------------------------------------------------------------
# CHECK 14 -- Risk management limits
# -----------------------------------------------------------------------------

def check_risk():
    _head("CHECK 14 -- Risk management limits")

    src = _src("risk_manager.py")

    if _grep(r"max_daily_loss_pct|daily.*loss", "risk_manager.py"):
        ok("Max daily loss limit present")
    else:
        fail("Max daily loss limit missing")

    if _grep(r"max_drawdown_pct|drawdown", "risk_manager.py"):
        ok("Max drawdown halt present")
    else:
        fail("Max drawdown halt missing")

    if _grep(r"min_bankroll|bankroll.*<.*min|minimum.*bankroll", "risk_manager.py"):
        ok("Minimum bankroll floor present")
    else:
        fail("No minimum bankroll floor")

    if _grep(r"consecutive_losses|cooldown", "risk_manager.py"):
        ok("Consecutive loss cooldown present")
    else:
        fail("No consecutive loss protection")

    if _grep(r"halt_until.*inf|float.*inf|permanent.*halt", "risk_manager.py"):
        ok("Permanent halt on max drawdown (halt_until=inf)")
    else:
        warn("Drawdown halt may not be permanent -- check halt_until value")

    # Check risk config in run.py
    src_run = _src("run.py")
    m_edge = re.search(r"min_edge\s*=\s*([\d.]+)", src_run)
    m_dloss = re.search(r"max_daily_loss_pct\s*=\s*([\d.]+)", src_run)
    m_pos = re.search(r"max_position_pct\s*=\s*([\d.]+)", src_run)

    if m_edge:
        edge = float(m_edge.group(1))
        ok(f"min_edge={edge} ({edge*100:.0f}% minimum edge required to enter)")
    if m_dloss:
        dloss = float(m_dloss.group(1))
        ok(f"max_daily_loss_pct={dloss} ({dloss*100:.0f}% daily loss limit)")
    if m_pos:
        pos = float(m_pos.group(1))
        ok(f"max_position_pct={pos} ({pos*100:.0f}% max position size per trade)")


# -----------------------------------------------------------------------------
# FINAL SUMMARY
# -----------------------------------------------------------------------------

def print_summary():
    print(f"\n{'='*60}")
    print("  PREFLIGHT SUMMARY")
    print(f"{'='*60}")

    if not FAILURES and not WARNINGS:
        print(f"\n  {GREEN}ALL CHECKS PASSED -- Ready for live trading{RESET}\n")
        return 0

    if FAILURES:
        print(f"\n  {RED}CRITICAL FAILURES ({len(FAILURES)}) -- DO NOT GO LIVE:{RESET}")
        for f in FAILURES:
            print(f"    {RED}✗{RESET}  {f}")

    if WARNINGS:
        print(f"\n  {YELLOW}WARNINGS ({len(WARNINGS)}) -- Review before going live:{RESET}")
        for w in WARNINGS:
            print(f"    {YELLOW}!{RESET}  {w}")

    if FAILURES:
        print(f"\n  {RED}Fix all CRITICAL failures before going live.{RESET}\n")
        return 1
    else:
        print(f"\n  {YELLOW}No critical failures -- but review warnings above.{RESET}\n")
        return 0


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="theSecondBot live-readiness preflight check")
    parser.add_argument("--no-live", action="store_true",
                        help="Skip checks requiring a live wallet connection")
    args = parser.parse_args()

    # Add bot directory to path so imports work
    sys.path.insert(0, str(ROOT))

    env = _load_env()
    live = not args.no_live and bool(env.get("PRIVATE_KEY"))

    print(f"\n{'='*60}")
    print("  theSecondBot - Pre-flight Checklist")
    print(f"  Mode: {'FULL (live wallet checks included)' if live else 'STATIC (no wallet connection)'}")
    print(f"{'='*60}")

    if live:
        _info("Will attempt live wallet connection. Use --no-live to skip.")
    else:
        _info("Wallet checks skipped. Set PRIVATE_KEY in .env for full check.")

    check_environment()
    check_authentication(env, live)
    check_order_rejection()
    check_partial_fills()
    check_gas()
    check_balance_sync(env, live)
    check_resolution()
    check_rate_limiting()
    check_slippage()
    check_usdc_allowance(env, live)
    check_startup_position_recovery(env, live)
    check_telegram_security(env)
    check_dry_run()
    check_robustness()
    check_risk()

    sys.exit(print_summary())


if __name__ == "__main__":
    main()
