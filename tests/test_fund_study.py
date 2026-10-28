"""Tests for the fundamentals study (D51): parsers and, above all, the time alignment (no look-ahead)."""
import datetime as dt
import json

import numpy as np
import pandas as pd

from backtest import fund_data as F
from backtest import fund_study as S

D = dt.date


def cal_days(start=D(2024, 1, 1), n=60):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


# ------------------------------------------------------------------ alignment

def test_revenue_available_month_boundary_is_day_11():
    assert F.revenue_available_month(D(2024, 3, 10)) == pd.Period("2024-01", "M")  # Feb figure not yet safe
    assert F.revenue_available_month(D(2024, 3, 11)) == pd.Period("2024-02", "M")
    assert F.revenue_available_month(D(2024, 3, 31)) == pd.Period("2024-02", "M")
    assert F.revenue_available_month(D(2024, 1, 5)) == pd.Period("2023-11", "M")  # year wrap
    assert F.revenue_available_month(D(2024, 1, 11)) == pd.Period("2023-12", "M")


def test_cutoff_trading_day_is_strictly_before_entry():
    cal = cal_days()
    e = cal[10]
    assert F.cutoff_trading_day(cal, e, 1) == cal[9]
    assert F.cutoff_trading_day(cal, e, 2) == cal[8]
    # entry on a non-trading day (weekend): the last trading day before it, still never the entry date itself
    sat = cal[4] + dt.timedelta(days=1)
    assert F.cutoff_trading_day(cal, sat, 1) == cal[4]
    assert F.cutoff_trading_day(cal, cal[0], 1) is None


def test_revenue_features_ignore_future_months():
    ix = pd.period_range("2023-01", "2024-06", freq="M")
    yoy = pd.Series(np.linspace(-0.1, 0.5, len(ix)), index=ix)
    avail = pd.Period("2024-02", "M")
    base = S.revenue_features(yoy, avail)
    poisoned = yoy.copy()
    poisoned[ix > avail] = 99.0  # months published after the as-of date
    assert S.revenue_features(poisoned, avail) == base
    assert base["yoy1"] == yoy.loc[avail]
    assert base["yoy_high12"] == 1.0  # monotone rising series: always a new high


def test_revenue_features_hand_numbers():
    ix = pd.period_range("2023-01", "2023-12", freq="M")
    vals = [0.10, 0.20, 0.50, 0.10, 0.00, -0.1, 0.05, 0.02, 0.03, 0.04, 0.30, 0.40]
    yoy = pd.Series(vals, index=ix)
    f = S.revenue_features(yoy, pd.Period("2023-12", "M"))
    assert f["yoy1"] == 0.40
    assert abs(f["yoy3"] - (0.04 + 0.30 + 0.40) / 3) < 1e-12
    assert f["yoy_high12"] == 0.0  # 0.50 in March is higher
    # fewer than 12 months of history -> undefined, not guessed
    assert np.isnan(S.revenue_features(yoy, pd.Period("2023-06", "M"))["yoy_high12"])
    assert np.isnan(S.revenue_features(yoy, pd.Period("2024-03", "M"))["yoy1"])  # month not in the series


def test_inst_panel_sums_and_truncation():
    cal = cal_days(n=30)
    net = pd.DataFrame({"date": cal, "foreign_net": np.arange(30) * 1000.0, "trust_net": np.full(30, 10.0)})
    vol = pd.Series(np.full(30, 100000.0), index=cal)
    p = S.inst_panel(cal, set(cal), net, vol)
    i = 24
    assert p["f5_raw"].iloc[i] == sum(range(20, 25)) * 1000.0
    assert abs(p["f5"].iloc[i] - sum(range(20, 25)) * 1000.0 / (5 * 100000.0)) < 1e-12
    assert p["t20_raw"].iloc[i] == 200.0
    assert np.isnan(p["f20"].iloc[18])  # fewer than 20 days
    # poison every day after i: the value at i must not move (as-of computation)
    net2 = net.copy()
    net2.loc[net2["date"] > cal[i], ["foreign_net", "trust_net"]] = 1e12
    p2 = S.inst_panel(cal, set(cal), net2, vol)
    assert p2["f20"].iloc[i] == p["f20"].iloc[i] and p2["ft5"].iloc[i] == p["ft5"].iloc[i]


def test_inst_panel_missing_report_day_is_nan_but_absent_symbol_is_zero():
    cal = cal_days(n=30)
    net = pd.DataFrame({"date": cal[:10] + cal[11:], "foreign_net": 1.0, "trust_net": 1.0})  # symbol absent cal[10]
    vol = pd.Series(1000.0, index=cal)
    p = S.inst_panel(cal, set(cal), net, vol)
    assert p["f5_raw"].iloc[12] == 4.0  # the absent day counts as 0
    p2 = S.inst_panel(cal, set(cal) - {cal[10]}, net, vol)  # report for that day was never downloaded
    assert np.isnan(p2["f5"].iloc[12]) and not np.isnan(p2["f5"].iloc[16])


def test_signal_row_uses_only_pre_entry_data():
    cal = cal_days(D(2024, 1, 1), 80)
    net = pd.DataFrame({"date": cal, "foreign_net": np.arange(80) * 1.0, "trust_net": 0.0})
    vol = pd.Series(1.0e4, index=cal)
    panel = S.inst_panel(cal, set(cal), net, vol)
    ix = pd.period_range("2022-12", "2024-03", freq="M")
    yoy = pd.Series(np.arange(len(ix)) / 100.0, index=ix)
    entry = cal[40]
    r1 = S.signal_row(entry, yoy, panel, cal, lag=1)
    r2 = S.signal_row(entry, yoy, panel, cal, lag=2)
    assert r1["inst_cutoff"] == cal[39].isoformat() and r2["inst_cutoff"] == cal[38].isoformat()
    assert r1["f5_raw"] == sum(range(35, 40)) and r2["f5_raw"] == sum(range(34, 39))
    # entry date + revenue rule: cal[40] is 2024-02-26 -> January revenue is the latest published month
    assert r1["rev_avail_month"] == "2024-01" and r1["yoy1"] == yoy.loc[pd.Period("2024-01", "M")]
    # changing the entry day's own institutional numbers or any later one changes nothing
    net2 = net.copy()
    net2.loc[net2["date"] >= entry, ["foreign_net"]] = 9e9
    r3 = S.signal_row(entry, yoy, S.inst_panel(cal, set(cal), net2, vol), cal, lag=1)
    assert r3["f5"] == r1["f5"] and r3["f20"] == r1["f20"]


def test_apply_rule_nan_fails():
    d = pd.DataFrame({"a": [1.0, -1.0, np.nan], "b": [1.0, 1.0, 1.0]})
    assert list(S.apply_rule(d, {"a": 0.0, "b": 0.0})) == [True, False, False]
    assert list(S.apply_rule(d, {})) == [True, True, True]


def test_tw_date_conversion():
    assert S.tw_date(pd.Timestamp("2024-03-04 01:00", tz="UTC")) == D(2024, 3, 4)


# ------------------------------------------------------------------ parsers

def test_parse_t86_twse_and_tpex_layouts():
    tw = {"stat": "OK", "fields": ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
                                   "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數",
                                   "外資自營商買賣超股數", "投信買進股數", "投信賣出股數", "投信買賣超股數"],
          "data": [["2330", "台積電", "9", "1", "1,000", "0", "0", "-200", "5", "1", "300"]]}
    r = F.parse_t86("twse", json.dumps(tw).encode())
    assert r == [{"code": "2330", "foreign_net": 800.0, "trust_net": 300.0}]
    old = {"stat": "OK", "fields": ["證券代號", "證券名稱", "外資買進股數", "外資賣出股數", "外資買賣超股數",
                                    "投信買進股數", "投信賣出股數", "投信買賣超股數"],
           "data": [["2409", "友達", "1", "1", "28,375,197", "0", "17,000", "-17,000"]]}
    assert F.parse_t86("twse", json.dumps(old).encode())[0] == {"code": "2409", "foreign_net": 28375197.0,
                                                                "trust_net": -17000.0}
    assert F.parse_t86("twse", json.dumps({"stat": "很抱歉，沒有符合條件的資料!"}).encode()) == []
    row25 = ["6488", "x"] + ["0"] * 23
    row25[10], row25[13] = "-643,883", "8,000"
    t25 = {"tables": [{}, {"data": [row25]}]}
    assert F.parse_t86("tpex", json.dumps(t25).encode()) == [{"code": "6488", "foreign_net": -643883.0,
                                                                "trust_net": 8000.0}]
    row17 = ["6488", "x"] + ["0"] * 15
    row17[4], row17[7] = "5", "-6"
    assert F.parse_t86("tpex", json.dumps({"tables": [{"data": [row17]}]}).encode())[0]["trust_net"] == -6.0


def test_parse_revenue_html():
    html = ("<table><tr align=right><td align=center>1101</td><td align=left>台泥</td>"
            "<td nowrap>  9,832,764</td><td nowrap>9,672,128</td><td nowrap>9,872,865</td><td nowrap>1.66</td>"
            "<Td nowrap>-0.40</Td><td nowrap>41,542,544</td><td nowrap>1</td><td nowrap>5.13</td>"
            "<td align=left>-</td></tr><tr align=right><td align=center>合計</td><td>x</td><td>1</td><td>1</td>"
            "<td>1</td><td>1</td><td>1</td></tr></table>").encode("big5")
    r = F.parse_revenue_html(html)
    assert len(r) == 1 and r[0]["code"] == "1101" and r[0]["rev"] == 9832764.0 and r[0]["yoy_pct"] == -0.40
