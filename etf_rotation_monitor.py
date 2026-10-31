#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A股免五 ETF 高胜率双标的阈值轮动策略 - 盘中实时监控与邮件预警系统
===================================================================
运行频率: 每个交易日 14:30 自动触发
目标资金: 1,000 ~ 5,000 元 (支持 100 股整手建仓与免五佣金测算)
监控标的: 10 只高流动性、免五、单手百元级 A 股行业与风格 ETF:
  - 科技成长: 半导设备 (159558), 人工智能 (515980), 通信 (515050), 机器人 (562500)
  - 顺周期:   有色金属 (512400), 煤炭 (515220), 石油 (561360)
  - 防御金融: 红利低波 (512890), 银行 (512800), 电力 (159611)

核心逻辑:
  1. 实时获取 10 只 ETF 最近 100 日标准前复权 (QFQ) 日 K 线及盘中最新成交价
  2. 计算 20 日风险调整动量、5/20日动量加速度与截面 Z-score 复合得分
  3. 识别 89 日波动率高位触轨反弹 (胜率 70%~83% 战术席位)
  4. 监测 10-ETF 等权市场基准与 MA20 趋势 (大盘 Regime 状态熔断)
  5. 测算 1,000 元 与 5,000 元小微本金下的整手买入股数、花费金额与剩余备用金
  6. 渲染精美 HTML + 纯文本内参，通过 SMTP SSL 推送至 QQ 邮箱
"""

import os
import sys
import json
import math
import smtplib
import ssl
import urllib.request
from datetime import datetime, timezone, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# 设置终端输出编码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BEIJING_TZ = timezone(timedelta(hours=8))

# 10 只免五核心标的配置字典
ETF_UNIVERSE = {
    "159558": {"name": "半导设备", "symbol": "sz159558", "group": "科技成长"},
    "515980": {"name": "人工智能", "symbol": "sh515980", "group": "科技成长"},
    "515050": {"name": "通信",     "symbol": "sh515050", "group": "科技成长"},
    "562500": {"name": "机器人",   "symbol": "sh562500", "group": "科技成长"},
    "512400": {"name": "有色金属", "symbol": "sh512400", "group": "顺周期"},
    "515220": {"name": "煤炭",     "symbol": "sh515220", "group": "顺周期"},
    "561360": {"name": "石油",     "symbol": "sh561360", "group": "顺周期"},
    "512890": {"name": "红利低波", "symbol": "sh512890", "group": "防御金融"},
    "512800": {"name": "银行",     "symbol": "sh512800", "group": "防御金融"},
    "159611": {"name": "电力",     "symbol": "sz159611", "group": "防御金融"},
}


def http_get_direct(url, timeout=15):
    """绕过代理直接发起 HTTP 请求"""
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def fetch_etf_history_and_live(code_info):
    """
    拉取单只 ETF 历史 100 日前复权 (QFQ) 日线，并融合今日 14:30 盘中最新价
    返回清洗对齐后的行情序列列表，每个元素为:
    {'date': 'YYYY-MM-DD', 'open': float, 'high': float, 'low': float, 'close': float, 'vol': float}
    """
    symbol = code_info["symbol"]
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,100,qfq"
    content = http_get_direct(url)
    data = json.loads(content.decode("utf-8"))
    stock_data = data.get("data", {}).get(symbol, {})
    k_list = stock_data.get("qfqday", stock_data.get("day", []))

    records = []
    for item in k_list:
        # 格式: [date, open, close, high, low, volume]
        dt = item[0]
        o = float(item[1])
        c = float(item[2])
        h = float(item[3])
        l = float(item[4])
        v = float(item[5])
        records.append({"date": dt, "open": o, "high": h, "low": l, "close": c, "vol": v})

    # 获取盘中实时五档行情
    try:
        live_url = f"http://qt.gtimg.cn/q={symbol}"
        live_raw = http_get_direct(live_url).decode("gbk", errors="ignore")
        if "=" in live_raw:
            parts = live_raw.split("=")[1].strip('";\n').split("~")
            if len(parts) > 35:
                live_price = float(parts[3])
                live_open = float(parts[5]) if float(parts[5]) > 0 else live_price
                live_high = float(parts[33]) if float(parts[33]) > 0 else live_price
                live_low = float(parts[34]) if float(parts[34]) > 0 else live_price
                live_vol = float(parts[6]) * 100  # 手转股
                today_str = parts[30][:8]  # YYYYMMDD
                today_fmt = f"{today_str[:4]}-{today_str[4:6]}-{today_str[6:8]}"

                if live_price > 0:
                    if records and records[-1]["date"] == today_fmt:
                        # 更新今日盘中收盘价与最高最低
                        records[-1]["close"] = live_price
                        records[-1]["high"] = max(records[-1]["high"], live_high)
                        records[-1]["low"] = min(records[-1]["low"], live_low)
                        records[-1]["vol"] = max(records[-1]["vol"], live_vol)
                    else:
                        # 盘中新增今日 K 线
                        records.append({
                            "date": today_fmt,
                            "open": live_open,
                            "high": live_high,
                            "low": live_low,
                            "close": live_price,
                            "vol": live_vol
                        })
    except Exception as e:
        print(f"[Warn] 获取 {code_info['name']} 盘中最新快照失败, 沿用日线: {e}")

    return records


def calculate_factors_for_etf(records):
    """
    基于单个 ETF 历史日线计算关键因子值
    """
    if len(records) < 90:
        return None

    closes = [r["close"] for r in records]
    highs = [r["high"] for r in records]
    lows = [r["low"] for r in records]
    opens = [r["open"] for r in records]

    current_close = closes[-1]
    prev_close = closes[-2]
    today_open = opens[-1]

    # 1. 20 日真实波幅 (ATR20)
    trs = []
    for i in range(1, len(records)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        trs.append(max(tr1, tr2, tr3))

    atr20 = sum(trs[-20:]) / 20.0 if len(trs) >= 20 else 0.01

    # 2. 20 日风险调整动量与 5 日动量
    close_20d_ago = closes[-21] if len(closes) >= 21 else closes[0]
    close_5d_ago = closes[-6] if len(closes) >= 6 else closes[0]

    risk_adj_mom20 = (current_close - close_20d_ago) / (atr20 * math.sqrt(20) + 1e-8)
    mom_5d = (current_close / close_5d_ago) - 1.0
    mom_20d = (current_close / close_20d_ago) - 1.0

    # 3. 5/20 动量加速度
    accel5_20 = mom_5d - 0.25 * mom_20d

    # 4. 20 日均线 (MA20)
    ma20 = sum(closes[-20:]) / 20.0
    above_ma20 = current_close >= ma20

    # 5. 布林带 (20 日, 2 倍标准差)
    c_slice = closes[-20:]
    mean_20 = sum(c_slice) / 20.0
    std_20 = math.sqrt(sum((x - mean_20) ** 2 for x in c_slice) / 20.0)
    boll_lower = mean_20 - 2.0 * std_20
    boll_bias = (current_close - boll_lower) / (boll_lower + 1e-8)

    # 6. 10 日历史收益波动率在 89 日内的分位数 (Panic Volatility Rank)
    daily_rets = [(closes[i] / closes[i - 1]) - 1.0 for i in range(1, len(closes))]
    vol10_series = []
    for i in range(10, len(daily_rets) + 1):
        window = daily_rets[i - 10:i]
        m_win = sum(window) / 10.0
        v_win = math.sqrt(sum((x - m_win) ** 2 for x in window) / 10.0)
        vol10_series.append(v_win)

    if len(vol10_series) >= 89:
        vol_89_window = vol10_series[-89:]
        current_vol = vol_89_window[-1]
        panic_vol_rank = sum(1 for v in vol_89_window if current_vol >= v) / float(len(vol_89_window))
    else:
        panic_vol_rank = 0.5

    # 7. 恐慌触轨高胜率反弹触发判定
    is_panic_bounce = (panic_vol_rank >= 0.85) and (current_close <= boll_lower * 1.01)

    # 8. 晨间跳空过滤
    morning_gap = (today_open / prev_close) - 1.0
    gap_warning = morning_gap > 0.015  # 高开跳空超 1.5%

    return {
        "current_close": current_close,
        "prev_close": prev_close,
        "daily_change_pct": (current_close / prev_close - 1.0) * 100,
        "atr20": atr20,
        "risk_adj_mom20": risk_adj_mom20,
        "mom_5d_pct": mom_5d * 100,
        "mom_20d_pct": mom_20d * 100,
        "accel5_20": accel5_20,
        "ma20": ma20,
        "above_ma20": above_ma20,
        "boll_lower": boll_lower,
        "boll_bias_pct": boll_bias * 100,
        "panic_vol_rank": panic_vol_rank,
        "is_panic_bounce": is_panic_bounce,
        "morning_gap_pct": morning_gap * 100,
        "gap_warning": gap_warning,
    }


def compute_portfolio_signals():
    """
    主计算流程: 遍历 10 只 ETF 计算得分，获取大盘 Regime 状态，输出完整调仓指引
    """
    raw_data = {}
    indicators = {}

    for code, info in ETF_UNIVERSE.items():
        records = fetch_etf_history_and_live(info)
        raw_data[code] = records
        fac = calculate_factors_for_etf(records)
        if fac:
            indicators[code] = fac

    # 计算 10-ETF 等权市场基准指数与 MA20
    all_dates = sorted(list(set.intersection(*[set(r["date"] for r in raw_data[c]) for c in ETF_UNIVERSE])))
    ew_history = []
    base_prices = {c: [r["close"] for r in raw_data[c] if r["date"] == all_dates[0]][0] for c in ETF_UNIVERSE}

    for dt in all_dates:
        norm_sum = 0.0
        for c in ETF_UNIVERSE:
            p = [r["close"] for r in raw_data[c] if r["date"] == dt][0]
            norm_sum += (p / base_prices[c]) * 1000.0
        ew_history.append({"date": dt, "ew_index": norm_sum / len(ETF_UNIVERSE)})

    ew_closes = [h["ew_index"] for h in ew_history]
    ew_current = ew_closes[-1]
    ew_ma20 = sum(ew_closes[-20:]) / 20.0
    ew_ma20_prev5 = sum(ew_closes[-25:-5]) / 20.0
    ew_ma20_slope = (ew_ma20 - ew_ma20_prev5) / 5.0

    # 大盘 Regime 判定: 连续 2 日跌破 MA20 且斜率向下判定为空头防守
    is_below_ma20_today = ew_closes[-1] < ew_ma20
    is_below_ma20_yesterday = ew_closes[-2] < (sum(ew_closes[-21:-1]) / 20.0)
    is_bear_regime = is_below_ma20_today and is_below_ma20_yesterday and (ew_ma20_slope < 0)

    # 截面计算 Z-Score 复合动量分
    mom_vals = [indicators[c]["risk_adj_mom20"] for c in indicators]
    acc_vals = [indicators[c]["accel5_20"] for c in indicators]

    m_mean = sum(mom_vals) / len(mom_vals)
    m_std = math.sqrt(sum((x - m_mean) ** 2 for x in mom_vals) / len(mom_vals)) + 1e-8
    a_mean = sum(acc_vals) / len(acc_vals)
    a_std = math.sqrt(sum((x - a_mean) ** 2 for x in acc_vals) / len(acc_vals)) + 1e-8

    scored_list = []
    for code, ind in indicators.items():
        z_mom = (ind["risk_adj_mom20"] - m_mean) / m_std
        z_acc = (ind["accel5_20"] - a_mean) / a_std
        composite_score = z_mom + z_acc

        # 战术反弹优先级标记
        is_tactical = ind["is_panic_bounce"]

        scored_list.append({
            "code": code,
            "name": ETF_UNIVERSE[code]["name"],
            "group": ETF_UNIVERSE[code]["group"],
            "price": ind["current_close"],
            "change_pct": ind["daily_change_pct"],
            "mom20_pct": ind["mom_20d_pct"],
            "mom5_pct": ind["mom_5d_pct"],
            "composite_score": composite_score,
            "above_ma20": ind["above_ma20"],
            "is_tactical": is_tactical,
            "gap_warning": ind["gap_warning"],
            "panic_rank": ind["panic_vol_rank"],
            "boll_bias": ind["boll_bias_pct"],
        })

    # 排序逻辑:
    # 1. 战术反弹标的优先享受置换权
    # 2. 进攻动量标的按复合得分降序排序 (必须在 MA20 上方方可作为进攻候选)
    scored_list.sort(key=lambda x: (x["is_tactical"], x["above_ma20"], x["composite_score"]), reverse=True)

    # 选定 Top-2
    if is_bear_regime:
        # 空头防守模式: 50% 红利低波 + 50% 现金
        target_etf_1 = "512890"  # 红利低波
        target_etf_2 = "CASH"    # 现金
        target_reason = "大盘连续破位空头防守: 50% 配红利低波锁定下行风险，50% 持现金避险"
    else:
        target_etf_1 = scored_list[0]["code"]
        target_etf_2 = scored_list[1]["code"]
        target_reason = f"多头进攻优选: 【{scored_list[0]['name']}】(得分 {scored_list[0]['composite_score']:+.2f}) + 【{scored_list[1]['name']}】(得分 {scored_list[1]['composite_score']:+.2f})"

    return {
        "as_of_date": all_dates[-1],
        "is_bear_regime": is_bear_regime,
        "ew_current": ew_current,
        "ew_ma20": ew_ma20,
        "ew_ma20_slope": ew_ma20_slope,
        "ranked_etfs": scored_list,
        "target_etf_1": target_etf_1,
        "target_etf_2": target_etf_2,
        "target_reason": target_reason,
    }


def calculate_lot_execution(target_code, price, budget):
    """
    按 100 股整手计算单只标的的买入计划
    """
    if target_code == "CASH" or price <= 0:
        return {"lots": 0, "shares": 0, "cost": 0.0, "cash_left": budget}

    lot_cost = price * 100.0
    lots = int(budget // lot_cost)
    shares = lots * 100
    actual_cost = shares * price
    # 考虑万1免五佣金
    fee = actual_cost * 0.0001
    cash_left = budget - actual_cost - fee
    return {
        "lots": lots,
        "shares": shares,
        "cost": round(actual_cost, 2),
        "fee": round(fee, 2),
        "cash_left": round(cash_left, 2)
    }


def build_email_content(analysis):
    """
    生成针对 QQ 邮箱渲染的精美 HTML 与纯文本内容
    """
    as_of = analysis["as_of_date"]
    is_bear = analysis["is_bear_regime"]
    ranked = analysis["ranked_etfs"]

    # 标的1与标的2信息
    t1_code = analysis["target_etf_1"]
    t2_code = analysis["target_etf_2"]

    t1_name = "现金储备 (防守)" if t1_code == "CASH" else ETF_UNIVERSE[t1_code]["name"]
    t2_name = "现金储备 (防守)" if t2_code == "CASH" else ETF_UNIVERSE[t2_code]["name"]

    p1 = 1.0 if t1_code == "CASH" else [x["price"] for x in ranked if x["code"] == t1_code][0]
    p2 = 1.0 if t2_code == "CASH" else [x["price"] for x in ranked if x["code"] == t2_code][0]

    # 计算 1000 元 与 5000 元整手配置
    exec_1k_t1 = calculate_lot_execution(t1_code, p1, 500.0)
    exec_1k_t2 = calculate_lot_execution(t2_code, p2, 500.0)

    exec_5k_t1 = calculate_lot_execution(t1_code, p1, 2500.0)
    exec_5k_t2 = calculate_lot_execution(t2_code, p2, 2500.0)

    # 状态色板
    status_title = "🟡 空头防守模式 (切换红利低波+现金)" if is_bear else "🟢 多头进攻模式 (持有最强双龙头)"
    status_bg = "#fff8e6" if is_bear else "#e6f9f0"
    status_color = "#b78103" if is_bear else "#0e8a16"

    # 表格行 HTML
    table_rows_html = ""
    for idx, r in enumerate(ranked, 1):
        is_picked = r["code"] in [t1_code, t2_code]
        row_bg = "#f0f7ff" if is_picked else ("#ffffff" if idx % 2 == 1 else "#fcfcfc")
        pick_badge = '<span style="background:#1890ff;color:#fff;padding:2px 6px;border-radius:4px;font-size:12px;">入选持仓</span>' if is_picked else ""
        tactical_badge = '<span style="background:#fa8c16;color:#fff;padding:2px 6px;border-radius:4px;font-size:12px;">恐慌反弹</span>' if r["is_tactical"] else ""
        gap_badge = '<span style="background:#ff4d4f;color:#fff;padding:2px 6px;border-radius:4px;font-size:12px;">晨间高开跳空</span>' if r["gap_warning"] else ""

        chg_color = "#cf1322" if r["change_pct"] >= 0 else "#389e0d"
        table_rows_html += f"""
        <tr style="background:{row_bg};text-align:center;border-bottom:1px solid #e8e8e8;">
            <td style="padding:10px 6px;font-weight:bold;">{idx}</td>
            <td style="padding:10px 6px;text-align:left;"><b>{r['name']}</b> ({r['code']}) {pick_badge} {tactical_badge} {gap_badge}</td>
            <td style="padding:10px 6px;color:#555;">{r['group']}</td>
            <td style="padding:10px 6px;font-weight:bold;">{r['price']:.3f}</td>
            <td style="padding:10px 6px;color:{chg_color};font-weight:bold;">{r['change_pct']:+.2f}%</td>
            <td style="padding:10px 6px;">{r['mom20_pct']:+.1f}%</td>
            <td style="padding:10px 6px;font-weight:bold;color:#1d39c4;">{r['composite_score']:+.2f}</td>
            <td style="padding:10px 6px;">{'✅ 站上MA20' if r['above_ma20'] else '❌ 破MA20'}</td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:'PingFang SC','Microsoft YaHei',Arial,sans-serif;background-color:#f4f6f9;margin:0;padding:20px;color:#333;">
        <div style="max-width:800px;margin:0 auto;background:#fff;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,0.06);overflow:hidden;border:1px solid #e6ebf1;">
            
            <!-- 头部 Banner -->
            <div style="background:linear-gradient(135deg, #1890ff 0%, #0050b3 100%);padding:24px 30px;color:#fff;">
                <div style="font-size:24px;font-weight:bold;letter-spacing:1px;">🚀 A股免五 ETF 高胜率双标的轮动内参</div>
                <div style="font-size:14px;opacity:0.85;margin-top:6px;">盘中 14:30 自动监测 | 东财超级ETF免五专区 | 尾盘决策助手</div>
            </div>

            <div style="padding:24px 30px;">
                <!-- 核心状态卡片 -->
                <div style="background:{status_bg};border-left:6px solid {status_color};padding:16px 20px;border-radius:6px;margin-bottom:24px;">
                    <div style="font-size:18px;font-weight:bold;color:{status_color};">{status_title}</div>
                    <div style="font-size:14px;color:#555;margin-top:6px;line-height:1.6;">
                        <b>判定理由</b>: {analysis['target_reason']}<br>
                        <b>等权大盘基准</b>: {analysis['ew_current']:.1f}点 (MA20: {analysis['ew_ma20']:.1f}点, 斜率: {analysis['ew_ma20_slope']:+.2f})
                    </div>
                </div>

                <!-- 今日实操指令看板 -->
                <h3 style="border-bottom:2px solid #1890ff;padding-bottom:8px;margin-top:0;color:#1f1f1f;">🎯 今日 14:50 建议目标持仓 (各 50%)</h3>
                <div style="display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap;">
                    <div style="flex:1;min-width:240px;background:#f9fbfd;border:1px solid #d6e4ff;border-radius:8px;padding:16px;">
                        <div style="font-size:13px;color:#595959;">持仓标的 1 (50% 仓位)</div>
                        <div style="font-size:20px;font-weight:bold;color:#1890ff;margin:4px 0;">{t1_name} <span style="font-size:15px;color:#888;">{t1_code}</span></div>
                        <div style="font-size:14px;color:#333;">当前参考价: <b>{p1:.3f} 元</b></div>
                    </div>
                    <div style="flex:1;min-width:240px;background:#f9fbfd;border:1px solid #d6e4ff;border-radius:8px;padding:16px;">
                        <div style="font-size:13px;color:#595959;">持仓标的 2 (50% 仓位)</div>
                        <div style="font-size:20px;font-weight:bold;color:#1890ff;margin:4px 0;">{t2_name} <span style="font-size:15px;color:#888;">{t2_code}</span></div>
                        <div style="font-size:14px;color:#333;">当前参考价: <b>{p2:.3f} 元</b></div>
                    </div>
                </div>

                <!-- 小微资金实盘整手配置速查表 -->
                <h3 style="border-bottom:2px solid #1890ff;padding-bottom:8px;color:#1f1f1f;">💰 极小本金实盘建仓测算 (免五万1无最低5元，0印花税)</h3>
                <table style="width:100%;border-collapse:collapse;margin-bottom:24px;font-size:13px;">
                    <tr style="background:#fafafa;text-align:center;border-bottom:1px solid #e8e8e8;font-weight:bold;">
                        <th style="padding:8px;">账户资金</th>
                        <th style="padding:8px;">标的一配额 (50%)</th>
                        <th style="padding:8px;">建仓计划</th>
                        <th style="padding:8px;">标的二配额 (50%)</th>
                        <th style="padding:8px;">建仓计划</th>
                        <th style="padding:8px;">备用现金留存</th>
                    </tr>
                    <tr style="text-align:center;border-bottom:1px solid #e8e8e8;">
                        <td style="padding:10px;font-weight:bold;background:#fffbe6;">1,000 元</td>
                        <td style="padding:10px;">{t1_name} (500元)</td>
                        <td style="padding:10px;color:#1890ff;font-weight:bold;">买入 {exec_1k_t1['lots']} 手 ({exec_1k_t1['shares']}股)<br><span style="font-size:12px;color:#888;">耗资 {exec_1k_t1['cost']}元</span></td>
                        <td style="padding:10px;">{t2_name} (500元)</td>
                        <td style="padding:10px;color:#1890ff;font-weight:bold;">买入 {exec_1k_t2['lots']} 手 ({exec_1k_t2['shares']}股)<br><span style="font-size:12px;color:#888;">耗资 {exec_1k_t2['cost']}元</span></td>
                        <td style="padding:10px;color:#52c41a;font-weight:bold;">{exec_1k_t1['cash_left'] + exec_1k_t2['cash_left']:.2f} 元</td>
                    </tr>
                    <tr style="text-align:center;border-bottom:1px solid #e8e8e8;">
                        <td style="padding:10px;font-weight:bold;background:#fffbe6;">5,000 元</td>
                        <td style="padding:10px;">{t1_name} (2500元)</td>
                        <td style="padding:10px;color:#1890ff;font-weight:bold;">买入 {exec_5k_t1['lots']} 手 ({exec_5k_t1['shares']}股)<br><span style="font-size:12px;color:#888;">耗资 {exec_5k_t1['cost']}元</span></td>
                        <td style="padding:10px;">{t2_name} (2500元)</td>
                        <td style="padding:10px;color:#1890ff;font-weight:bold;">买入 {exec_5k_t2['lots']} 手 ({exec_5k_t2['shares']}股)<br><span style="font-size:12px;color:#888;">耗资 {exec_5k_t2['cost']}元</span></td>
                        <td style="padding:10px;color:#52c41a;font-weight:bold;">{exec_5k_t1['cash_left'] + exec_5k_t2['cash_left']:.2f} 元</td>
                    </tr>
                </table>

                <!-- 10 只 ETF 综合因子大榜 -->
                <h3 style="border-bottom:2px solid #1890ff;padding-bottom:8px;color:#1f1f1f;">📊 10 只免五核心 ETF 截面动量与信号天梯</h3>
                <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:20px;">
                    <thead>
                        <tr style="background:#fafafa;font-weight:bold;color:#555;border-bottom:2px solid #e8e8e8;">
                            <th style="padding:10px 6px;">排名</th>
                            <th style="padding:10px 6px;text-align:left;">ETF 标的</th>
                            <th style="padding:10px 6px;">分类</th>
                            <th style="padding:10px 6px;">最新价</th>
                            <th style="padding:10px 6px;">今日涨跌</th>
                            <th style="padding:10px 6px;">20日涨跌</th>
                            <th style="padding:10px 6px;">复合得分</th>
                            <th style="padding:10px 6px;">均线状态</th>
                        </tr>
                    </thead>
                    <tbody>
                        {table_rows_html}
                    </tbody>
                </table>

                <!-- 策略纪律底线提示 -->
                <div style="background:#f6f8fa;padding:14px 18px;border-radius:6px;font-size:12px;color:#666;line-height:1.6;">
                    <b>💡 交易纪律与执行守则</b>:<br>
                    1. <b>阈值防抽搐</b>: 若您当前已持有前 2 名之一，且新标的分差未超过 15%，<b>坚决不调仓</b>，避免横跳摩擦。<br>
                    2. <b>硬止损与动态止盈</b>: 单只持仓亏损达到 <b>-4.0%</b> 坚决止损转入现金或红利低波；浮盈达 <b>+7.0%</b> 后自最高点回撤 2.5% 动态止盈。<br>
                    3. <b>尾盘执行</b>: 建议在 14:50 ~ 14:55 以买一/卖一档限价单委托，规避市价单冲击。
                </div>
            </div>

            <!-- 底部说明 -->
            <div style="background:#fafafa;padding:16px 30px;font-size:12px;color:#8c8c8c;border-top:1px solid #f0f0f0;text-align:center;">
                A股免五 ETF 高胜率双标的轮动系统 | GitHub Actions 每日 14:30 自动生成 | 数据时间: {as_of}
            </div>
        </div>
    </body>
    </html>
    """

    # 纯文本备用内容
    text_lines = [
        f"【A股免五ETF高胜率双标的轮动内参】({as_of})",
        "=" * 50,
        f"状态: {status_title}",
        f"理由: {analysis['target_reason']}",
        "-" * 50,
        "【今日建议目标持仓 (各 50% 仓位)】",
        f"标的 1: {t1_name} ({t1_code}) - 参考价: {p1:.3f}",
        f"标的 2: {t2_name} ({t2_code}) - 参考价: {p2:.3f}",
        "-" * 50,
        "【1,000 元极小本金整手方案】",
        f"{t1_name}: 买入 {exec_1k_t1['lots']} 手 ({exec_1k_t1['shares']} 股), 耗资 {exec_1k_t1['cost']} 元",
        f"{t2_name}: 买入 {exec_1k_t2['lots']} 手 ({exec_1k_t2['shares']} 股), 耗资 {exec_1k_t2['cost']} 元",
        f"留存备用现金: {exec_1k_t1['cash_left'] + exec_1k_t2['cash_left']:.2f} 元",
        "-" * 50,
        "【5,000 元实盘整手方案】",
        f"{t1_name}: 买入 {exec_5k_t1['lots']} 手 ({exec_5k_t1['shares']} 股), 耗资 {exec_5k_t1['cost']} 元",
        f"{t2_name}: 买入 {exec_5k_t2['lots']} 手 ({exec_5k_t2['shares']} 股), 耗资 {exec_5k_t2['cost']} 元",
        f"留存备用现金: {exec_5k_t1['cash_left'] + exec_5k_t2['cash_left']:.2f} 元",
        "=" * 50,
        "【全天梯动量排名 Top 5】",
    ]
    for idx, r in enumerate(ranked[:5], 1):
        text_lines.append(f"{idx}. {r['name']}({r['code']}): 现价 {r['price']:.3f}, 复合得分 {r['composite_score']:+.2f}, 今日涨跌 {r['change_pct']:+.2f}%")

    text = "\n".join(text_lines)
    subject = f"【ETF轮动内参】{as_of} 盘中指引: {'防守' if is_bear else '多头优选'} [{t1_name}+{t2_name}]"

    return text, html, subject


def send_email(text_body, html_body, subject):
    """通过 QQ 邮箱 SMTP SSL 发送邮件"""
    user = os.environ.get("SMTP_USER", "").strip()
    code = os.environ.get("SMTP_CODE", "").strip()
    to = os.environ.get("SMTP_TO", "").strip() or user

    if not user or not code:
        print("[Info] 未配置 SMTP_USER / SMTP_CODE 环境变量，跳过邮件发送 (本地打印完整内参)")
        return False

    sender = user if "@" in user else f"{user}@qq.com"
    recipient = to if "@" in to else f"{to}@qq.com"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.qq.com", 465, context=context, timeout=25) as s:
            s.login(user, code)
            s.sendmail(sender, [recipient], msg.as_string())
        print(f"✅ 邮件已成功送达至 QQ 邮箱: {recipient}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ 邮件发送失败 (授权认证失败 535): {e}")
        print("💡 提示: QQ 邮箱授权码可能已失效，请前往 QQ 邮箱 -> 设置 -> 账户 -> 生成新的 16 位授权码，并在 GitHub 仓库 Settings -> Secrets 中更新 SMTP_CODE。")
        return False
    except Exception as e:
        print(f"❌ 邮件发送失败: {type(e).__name__}: {e}")
        print("💡 提示: 若返回 Connection unexpectedly closed，多因 QQ 邮箱服务 535 认证不通过后主动断开，请确认 SMTP_CODE 授权码是否有效。")
        return False


def main():
    print("=" * 60)
    print("启动 A股免五 ETF 高胜率双标的阈值轮动盘中监控...")
    print("=" * 60)

    analysis = compute_portfolio_signals()
    text, html, subject = build_email_content(analysis)

    print("\n" + text + "\n")

    sent = send_email(text, html, subject)
    if sent:
        print(">> 盘中监控推送成功！")
    else:
        print(">> 监控计算完成！")

    return 0


if __name__ == "__main__":
    sys.exit(main())
