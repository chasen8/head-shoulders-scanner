"""Historical walk-forward backtest of the IHS and triangle detectors (charter D40).

backtest/engine.py  pure logic (walk-forward signals, exit simulation, costs); no network
backtest/data.py    long-history fetch + cache under C:\\trading-data\\ihs-scanner-v2\\backtest\\data
backtest/run.py     CLI: fetch / run / report
backtest/report.py  aggregation + Excel
The detector code in ihs_scanner/ is only imported, never changed.
"""
