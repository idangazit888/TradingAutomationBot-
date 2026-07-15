"""For each REAL trade in the log: what did BTC actually do in that 5-min window?
Compares the bot's directional bet to the real Binance close-vs-open, to find out
WHY the real win rate (23%) is so far below the model's directional accuracy (85%).
"""
import requests, calendar, datetime
from math import floor

def fetch_1m(start_ms, end_ms):
    out=[]; t=start_ms
    while t<end_ms:
        b=requests.get('https://api.binance.com/api/v3/klines',
            params={'symbol':'BTCUSDT','interval':'1m','startTime':t,'endTime':end_ms,'limit':1000},timeout=20).json()
        if not b: break
        out+=b; t=b[-1][0]+60000
        if len(b)<1000: break
    return out

def dt(y,mo,d,h,mi,s): return int(calendar.timegm((y,mo,d,h,mi,s,0,0,0)))

# (entry_ts_utc, side_bet, real_outcome)
trades = [
 (dt(2026,6,3,16,26,3),"DOWN","WIN"),
 (dt(2026,6,3,16,31,32),"DOWN","LOSS"),
 (dt(2026,6,3,16,41,1),"DOWN","LOSS"),
 (dt(2026,6,3,18,21,0),"DOWN","LOSS"),
 (dt(2026,6,3,18,31,0),"DOWN","WIN"),
 (dt(2026,6,3,18,36,17),"DOWN","LOSS"),
 (dt(2026,6,3,20,6,3),"DOWN","LOSS"),
 (dt(2026,6,3,20,16,0),"UP","LOSS"),
 (dt(2026,6,3,23,57,13),"DOWN","LOSS"),
 (dt(2026,6,4,0,8,51),"UP","LOSS"),
 (dt(2026,6,4,0,16,2),"UP","LOSS"),
 (dt(2026,6,4,0,26,2),"DOWN","WIN"),
 (dt(2026,6,4,0,31,1),"DOWN","LOSS"),
]

kl = fetch_1m((trades[0][0]-3600)*1000, (trades[-1][0]+1200)*1000)
op = {c[0]//1000: float(c[1]) for c in kl}   # open by minute-ts
cl = {c[0]//1000: float(c[4]) for c in kl}   # close by minute-ts

def fmt(ts): return datetime.datetime.utcfromtimestamp(ts).strftime("%m-%d %H:%M")

print(f"{'entry(UTC)':12} {'bet':4} {'win_start':10} {'open':9} {'close':9} {'BTC move':9} {'actualDir':9} {'betRight':8} {'realLog':6}")
print("-"*95)
agree_dir=0; bet_right=0; n=0
for ets, side, real in trades:
    ws = (ets//300)*300            # aligned 5-min window containing the entry
    o = op.get(ws); c = cl.get(ws+240)
    if o is None or c is None:
        print(f"{fmt(ets):12} {side:4}  -- no candle --"); continue
    move = c - o
    actual = "UP" if c>=o else "DOWN"
    right = (actual==side)
    n+=1; bet_right+=right
    print(f"{fmt(ets):12} {side:4} {fmt(ws):10} {o:9.1f} {c:9.1f} {move:+9.1f} {actual:9} {str(right):8} {real:6}")
print("-"*95)
print(f"bot's bet matched real BTC window direction: {bet_right}/{n} = {bet_right/n*100:.0f}%")
print(f"(real-log win rate was 3/13 = 23%)")

# also test alternative window alignment: window = [entry rounded down to :00/:05] but resolution at NEXT boundary
print("\n-- if window were the PREVIOUS block (entry is in the LAST minute of prior window) --")
br2=0
for ets, side, real in trades:
    ws=((ets-60)//300)*300
    o=op.get(ws); c=cl.get(ws+240)
    if o and c:
        actual="UP" if c>=o else "DOWN"; br2+=(actual==side)
print(f"bet matched: {br2}/{n} = {br2/n*100:.0f}%")
