Skip to content
aliramman-art
crypto-pump-scanner
Repository navigation
Code
Issues
Pull requests
Actions
Projects
Wiki
Security and quality
Insights
Settings
VOLUME-KHAT 100
VOLUME-KHAT 100 #622
All jobs
Run details
Annotations
1 warning
scanner
succeeded 2 minutes ago in 23s
Search logs
1s
1s
0s
12s
0s
0s
2s
Run set -o pipefail
============================================================
VOLUME-KHAT 100
============================================================

SYSTEM: GitHub Actions
EXCHANGE: Kraken Futures
MARKETS: TOP 100 USD PERPETUAL

============================================================
STRATEGY
============================================================

1H  = Trend + Static Support/Resistance
15M = Dynamic Support/Resistance + RVOL + Setup
5M  = Entry Confirmation

============================================================
VOLUME
============================================================

RVOL baseline: 20 candles
Abnormal Volume: RVOL >= 2.0
Strong Volume: RVOL >= 3.0

============================================================
SETUPS
============================================================

BREAKOUT
HIGH-VOLUME REJECTION

============================================================
RISK MANAGEMENT
============================================================

Minimum RR: 1:2
Static S/R: 1H + 15M
Dynamic S/R: 15M
SL: Structural + ATR buffer
TP: Next valid static S/R

============================================================
STARTING SCANNER
============================================================

==================================================
KRAKEN FUTURES VOLUME-KHAT 100 v2.7 FIXED
PAPER TRADING ONLY
==================================================
[STEP 1] Checking existing trades...
[EXIT] PF_CRVUSD LONG -> LOSS @ 0.35142786 (SL) PnL=-0.727%
[EXIT] Newly closed: [14]
[STATS] OPEN=2 CLOSED=12 W=3 L=9 PnL=0.115%
[STEP 2] Discovering TOP 100 markets...
[MARKETS] Selected 0
[FATAL] No markets found.

============================================================
SCANNER EXIT CODE: 0
============================================================
0s
0s
1s
2s
0s
0s
0s
0s
