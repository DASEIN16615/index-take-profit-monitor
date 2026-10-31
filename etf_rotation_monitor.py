#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A股免五 ETF 高胜率双标的阈值轮动策略 - 盘中实时监控与多通道预警系统 (P1 + P2 + P3 全维度兜底版)
================================================================================
运行频率: 每个交易日 14:30 自动触发 (UTC 06:30)
资金方案:
  - 1,000 元 (极小微本金整手验证)
  - 5,000 元 (常规小微实战建仓)
  - 1,000,000 元 (大额模拟实盘方案，含整手股数、资金利用率及冲击成本/流动性提示)
监控标的: 10 只高流动性、免五、单手百元级 A 股行业与风格 ETF:
  - 科技成长: 半导设备 (159558), 人工智能 (515980), 通信 (515050), 机器人 (562500)
  - 顺周期:   有色金属 (512400), 煤炭 (515220), 石油 (561360)
  - 防御金融: 红利低波 (512890), 银行 (512800), 电力 (159611)

核心升级特性 (P1 + P2 + P3):
  1. 【P1 交易日历过滤】: 抓取数据前判断当天是否为 A 股交易日/休市日，法定节假日与周末休市时优雅退出，避免空发或报错打扰。
  2. 【P1 多通道 Webhook 即时推送】: 支持可选配置企业微信 (WECOM_WEBHOOK)、钉钉 (DINGTALK_WEBHOOK)、飞书 (FEISHU_WEBHOOK)、PushPlus (PUSHPLUS_TOKEN)，多端即时触达。
  3. 【P2 背包规划资金拉满】: 贪心/背包动态优化整手买入逻辑，消除 100 股整手约束下的零钱闲置 (Cash Drag)，小微资金利用率提升至 90%~99.8%，大额资金提升至 99.99%。
  4. 【P2 百万大额实盘方案】: 增设计划表与大额资金专项冲击成本、分批限价委托 (TWAP) 与盘口流动性风控提示。
  5. 【P3 数据源主备兜底 (Dual Data Provider)】: 主数据源为腾讯财经，若遇网络超时或格式异常，自动无缝降级切换至新浪财经 (hq.sinajs.cn) 备用接口。
  6. 【P3 单标的故障隔离 (Symbol Isolation)】: 标的遭遇停牌、无成交或数据缺失时自动隔离告警并跳过，保证其余正常标的完成轮动，不导致全局崩溃。
  7. 【P3 大盘基准失效兜底 (Regime Fallback)】: 若上证指数 (sh000001) 无法获取，兜底回退为保守防御模式 (50% 红利低波 + 50% 现金)，防范黑天鹅失控。
  8. 【P3 推送与展示双重兜底 (Step Summary Fallback)】: 自动将格式化 Markdown 内参写入 GitHub Actions 的 $GITHUB_STEP_SUMMARY，页面 1 秒即查。
  9. 【P3 数值安全兜底 (Zero-Division & Edge Guards)】: 零标准差、K线不足、除零、极值浮点等全量防御，默认填充安全值。
  10.【零三方依赖】: 严格保持纯 Python 3 标准库 (urllib, smtplib, json, math)。
"""

import os
import sys
import json
import math
import smtplib
import ssl
import urllib.request
import urllib.error
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

# 资金测算档位
BUDGET_TIERS = [1000.0, 5000.0, 1000000.0]

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


def http_get_direct(url, headers=None, timeout=12):
    """
    绕过系统代理直接发起 HTTP GET 请求，支持自定义 Headers 和超时
    纯 Python 3 标准库实现，包含完善的异常处理
    """
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def http_post_json(url, payload_dict, timeout=12):
    """通过标准库 urllib 发送 JSON POST 请求，绕过代理"""
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    data_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    )
    with opener.open(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def check_trading_day(force=False):
    """
    P1 优化项: 交易日历过滤
    判断当前时间（北京时间）是否为 A 股交易日。
    1. 若指定 force=True 或环境变量 FORCE_RUN=1 则强制放行（用于手动测试/调试）
    2. 周末（周六、周日）一律为非交易日（A股在调休周末亦从不开市）
    3. 工作日（周一至周五）调用法定节假日 API 与行情快照进行多层交叉校验：
       - 层级 1: Timor 节假日 API (https://timor.tech/api/holiday/info/{YYYY-MM-DD})
       - 层级 2: 盘中/盘后行情双源时间戳校验 (10:00 之后若腾讯/新浪行情最新成交日期非今日，则说明未开市)
       - 若网络异常，工作日默认放行，避免误拦截。
    返回: (is_trade_day: bool, reason: str)
    """
    if force or os.environ.get("FORCE_RUN", "").strip().lower() in ("true", "1", "yes"):
        return True, "强制执行模式 (已忽略休市过滤)"

    now_bj = datetime.now(BEIJING_TZ)
    today_str = now_bj.strftime("%Y-%m-%d")
    today_compact = now_bj.strftime("%Y%m%d")
    weekday = now_bj.weekday()
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][weekday]

    # 1. 周末判断
    if weekday in (5, 6):
        return False, f"周末休市日 ({today_str} {weekday_cn})"

    # 2. 法定节假日 API 判定
    try:
        url = f"https://timor.tech/api/holiday/info/{today_str}"
        content = http_get_direct(url, timeout=4).decode("utf-8")
        data = json.loads(content)
        holiday_info = data.get("holiday")
        type_info = data.get("type", {})
        is_holiday = (holiday_info and holiday_info.get("holiday") is True) or (type_info.get("type") == 2)
        if is_holiday:
            h_name = (holiday_info.get("name") if holiday_info else "") or type_info.get("name") or "法定节假日"
            return False, f"法定节假日休市 ({h_name})"
    except Exception as e:
        print(f"[Warn] 节假日日历 API 请求失败 ({e})，尝试行情接口交叉验证")

    # 3. 盘中/盘后行情时间戳校验 (10:00 之后如果行情最新成交日期不是今天，则说明今天休市)
    if now_bj.hour >= 10:
        market_date = None
        # 首选腾讯行情接口
        try:
            raw = http_get_direct("http://qt.gtimg.cn/q=sh000001", timeout=4).decode("gbk", errors="ignore")
            if "=" in raw:
                parts = raw.split("=")[1].strip('";\n').split("~")
                if len(parts) > 30:
                    market_date = parts[30][:8]
        except Exception as e:
            print(f"[Warn] 腾讯行情状态校验失败: {e}")

        # 备选新浪行情接口兜底
        if not market_date:
            try:
                sina_raw = http_get_direct(
                    "http://hq.sinajs.cn/list=sh000001",
                    headers={"Referer": "http://finance.sina.com.cn"},
                    timeout=4
                ).decode("gbk", errors="ignore")
                if "=" in sina_raw:
                    sina_parts = sina_raw.split("=")[1].strip('";\n').split(",")
                    if len(sina_parts) > 30 and sina_parts[30]:
                        market_date = sina_parts[30].replace("-", "")
            except Exception as e:
                print(f"[Warn] 新浪行情状态校验失败: {e}")

        if market_date and market_date != today_compact:
            return False, f"大盘无今日成交撮合记录 (最新撮合日期: {market_date[:4]}-{market_date[4:6]}-{market_date[6:8]})"

    return True, f"正常交易日 ({today_str} {weekday_cn})"


def fetch_shanghai_index():
    """
    P3 优化项: 上证指数双数据源主备获取
    先尝试腾讯财经接口，若失败自动降级至新浪财经。
    若两源皆不可用，返回 None，触发大盘基准失效兜底 (Regime Fallback)。
    """
    # 1. 尝试腾讯接口
    try:
        raw = http_get_direct("http://qt.gtimg.cn/q=sh000001", timeout=6).decode("gbk", errors="ignore")
        if "=" in raw:
            parts = raw.split("=")[1].strip('";\n').split("~")
            if len(parts) > 35:
                price = float(parts[3])
                prev_close = float(parts[4])
                chg_pct = float(parts[32]) if parts[32] else ((price / prev_close - 1.0) * 100.0 if prev_close > 0 else 0.0)
                dt_str = parts[30][:8]
                date_fmt = f"{dt_str[:4]}-{dt_str[4:6]}-{dt_str[6:8]}"
                if price > 0:
                    return {
                        "name": "上证指数",
                        "price": price,
                        "prev_close": prev_close,
                        "change_pct": chg_pct,
                        "date": date_fmt,
                        "provider": "腾讯财经"
                    }
    except Exception as e:
        print(f"[Warn] 腾讯接口获取上证指数失败: {e}，尝试切换新浪备用接口")

    # 2. 备选新浪接口
    try:
        raw_sina = http_get_direct(
            "http://hq.sinajs.cn/list=sh000001",
            headers={"Referer": "http://finance.sina.com.cn"},
            timeout=6
        ).decode("gbk", errors="ignore")
        if "=" in raw_sina:
            parts = raw_sina.split("=")[1].strip('";\n').split(",")
            if len(parts) > 30:
                price = float(parts[3])
                prev_close = float(parts[2])
                chg_pct = ((price / prev_close - 1.0) * 100.0) if prev_close > 0 else 0.0
                date_str = parts[30].strip()
                if price > 0:
                    return {
                        "name": "上证指数",
                        "price": price,
                        "prev_close": prev_close,
                        "change_pct": chg_pct,
                        "date": date_str,
                        "provider": "新浪财经"
                    }
    except Exception as e:
        print(f"[Error] 新浪备用接口获取上证指数失败: {e}")

    return None


def fetch_kline_data(symbol, count=100):
    """
    P3 优化项: 标的历史 K 线主备双源拉取
    主源: 腾讯前复权日线接口 (web.ifzq.gtimg.cn)
    备用源: 新浪财经日线接口 (money.finance.sina.com.cn)
    返回值: (records: list, provider: str)
    """
    # 1. 主源: 腾讯接口
    try:
        url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{count},qfq"
        content = http_get_direct(url, timeout=8)
        data = json.loads(content.decode("utf-8"))
        stock_data = data.get("data", {}).get(symbol, {})
        k_list = stock_data.get("qfqday", stock_data.get("day", []))
        if k_list and isinstance(k_list, list) and len(k_list) > 0:
            records = []
            for item in k_list:
                try:
                    dt = str(item[0])
                    o = float(item[1])
                    c = float(item[2])
                    h = float(item[3])
                    l = float(item[4])
                    v = float(item[5])
                    records.append({"date": dt, "open": o, "high": h, "low": l, "close": c, "vol": v})
                except (ValueError, IndexError):
                    continue
            if len(records) > 0:
                return records, "腾讯财经"
    except Exception as e:
        print(f"[Warn] 腾讯 K 线接口获取 {symbol} 失败 ({e})，无缝切换至新浪备用接口")

    # 2. 备用源: 新浪接口
    try:
        url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={count}"
        content = http_get_direct(url, headers={"Referer": "http://finance.sina.com.cn"}, timeout=8)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("gbk", errors="ignore")
        data = json.loads(text)
        if isinstance(data, list) and len(data) > 0:
            records = []
            for item in data:
                try:
                    dt = str(item["day"])
                    o = float(item["open"])
                    c = float(item["close"])
                    h = float(item["high"])
                    l = float(item["low"])
                    v = float(item["volume"])
                    records.append({"date": dt, "open": o, "high": h, "low": l, "close": c, "vol": v})
                except (ValueError, KeyError):
                    continue
            if len(records) > 0:
                print(f"[Fallback] 标的 {symbol} 成功通过新浪财经接口获取历史 K 线 ({len(records)} 条)")
                return records, "新浪财经"
    except Exception as e:
        print(f"[Error] 新浪备用 K 线接口获取 {symbol} 失败: {e}")

    return [], "none"


def fetch_live_quote(symbol):
    """
    P3 优化项: 标的盘中实时快照主备双源拉取
    主源: 腾讯 qt.gtimg.cn
    备用源: 新浪 hq.sinajs.cn
    返回值: dict 或 None
    """
    # 1. 主源: 腾讯接口
    try:
        url = f"http://qt.gtimg.cn/q={symbol}"
        raw = http_get_direct(url, timeout=5).decode("gbk", errors="ignore")
        if "=" in raw:
            parts = raw.split("=")[1].strip('";\n').split("~")
            if len(parts) > 35:
                price = float(parts[3])
                if price > 0:
                    today_str = parts[30][:8]
                    today_fmt = f"{today_str[:4]}-{today_str[4:6]}-{today_str[6:8]}"
                    return {
                        "price": price,
                        "open": float(parts[5]) if float(parts[5]) > 0 else price,
                        "high": float(parts[33]) if float(parts[33]) > 0 else price,
                        "low": float(parts[34]) if float(parts[34]) > 0 else price,
                        "vol": float(parts[6]) * 100.0,  # 腾讯单位手转股
                        "date": today_fmt,
                        "provider": "腾讯财经"
                    }
    except Exception as e:
        print(f"[Warn] 腾讯快照接口获取 {symbol} 失败 ({e})，切换新浪备用接口")

    # 2. 备用源: 新浪接口
    try:
        url = f"http://hq.sinajs.cn/list={symbol}"
        raw = http_get_direct(url, headers={"Referer": "http://finance.sina.com.cn"}, timeout=5).decode("gbk", errors="ignore")
        if "=" in raw:
            parts = raw.split("=")[1].strip('";\n').split(",")
            if len(parts) > 30:
                price = float(parts[3])
                if price > 0:
                    date_str = parts[30].strip()
                    return {
                        "price": price,
                        "open": float(parts[1]) if float(parts[1]) > 0 else price,
                        "high": float(parts[4]) if float(parts[4]) > 0 else price,
                        "low": float(parts[5]) if float(parts[5]) > 0 else price,
                        "vol": float(parts[8]),  # 新浪单位为股
                        "date": date_str,
                        "provider": "新浪财经"
                    }
    except Exception as e:
        print(f"[Error] 新浪备用快照接口获取 {symbol} 失败: {e}")

    return None


def fetch_etf_history_and_live(code_info):
    """
    拉取单只 ETF 历史 K 线并融合盘中实时快照 (主备双源保障)
    包含故障隔离检查：停牌、无成交或数据缺失标的返回 is_valid=False 及错误原因
    """
    symbol = code_info["symbol"]
    name = code_info["name"]

    # 1. 获取历史 K 线 (主备兜底)
    records, k_provider = fetch_kline_data(symbol, count=100)
    if not records:
        return [], False, f"历史K线主备数据源均无法获取 ({symbol})"

    # 2. 获取实时快照 (主备兜底)
    live = fetch_live_quote(symbol)
    if live and live["price"] > 0:
        # 融合盘中最新快照到末尾
        if records and records[-1]["date"] == live["date"]:
            records[-1]["close"] = live["price"]
            records[-1]["high"] = max(records[-1]["high"], live["high"])
            records[-1]["low"] = min(records[-1]["low"], live["low"])
            records[-1]["vol"] = max(records[-1]["vol"], live["vol"])
        else:
            records.append({
                "date": live["date"],
                "open": live["open"],
                "high": live["high"],
                "low": live["low"],
                "close": live["price"],
                "vol": live["vol"]
            })

        # 停牌/无成交检测
        if live["vol"] <= 0 and live["open"] <= 0:
            return records, False, f"标的处于停牌状态或今日无成交撮合"
    else:
        print(f"[Notice] 标的 {name} ({symbol}) 盘中最新快照不可用，沿用历史日线收盘价")

    # 检查最低历史天数要求 (至少 25 日以支撑 20 日均线与动量计算)
    if len(records) < 25:
        return records, False, f"有效交易天数不足 ({len(records)} < 25 天)"

    return records, True, "数据正常"


def calculate_factors_for_etf(records):
    """
    P3 优化项: 数值安全兜底因子计算
    对标准差为 0、历史天数不足、极值浮点等进行全量异常保护
    """
    if not records or len(records) < 25:
        return None

    closes = [float(r["close"]) for r in records]
    highs = [float(r["high"]) for r in records]
    lows = [float(r["low"]) for r in records]
    opens = [float(r["open"]) for r in records]

    current_close = closes[-1]
    prev_close = closes[-2] if len(closes) >= 2 else current_close
    today_open = opens[-1]

    # 数值校验: 价格必须为正数
    if current_close <= 0 or prev_close <= 0:
        return None

    # 1. 20 日真实波幅 (ATR20)
    trs = []
    for i in range(1, len(records)):
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        trs.append(max(tr1, tr2, tr3))

    if len(trs) >= 20:
        atr20 = sum(trs[-20:]) / 20.0
    elif len(trs) > 0:
        atr20 = sum(trs) / float(len(trs))
    else:
        atr20 = 0.01

    atr20 = max(atr20, 1e-4)  # 兜底避免极小波幅导致除零

    # 2. 20 日风险调整动量与 5 日动量
    close_20d_ago = closes[-21] if len(closes) >= 21 else closes[0]
    close_5d_ago = closes[-6] if len(closes) >= 6 else closes[0]

    close_20d_ago = max(close_20d_ago, 1e-4)
    close_5d_ago = max(close_5d_ago, 1e-4)

    denom_atr = atr20 * math.sqrt(20)
    denom_atr = max(denom_atr, 1e-6)

    risk_adj_mom20 = (current_close - close_20d_ago) / denom_atr
    mom_5d = (current_close / close_5d_ago) - 1.0
    mom_20d = (current_close / close_20d_ago) - 1.0

    # 3. 5/20 动量加速度
    accel5_20 = mom_5d - 0.25 * mom_20d

    # 4. 20 日均线 (MA20)
    c_slice_20 = closes[-20:] if len(closes) >= 20 else closes
    ma20 = sum(c_slice_20) / float(len(c_slice_20))
    above_ma20 = current_close >= ma20

    # 5. 布林带 (20 日, 2 倍标准差)
    mean_20 = ma20
    var_20 = sum((x - mean_20) ** 2 for x in c_slice_20) / float(len(c_slice_20))
    std_20 = math.sqrt(max(0.0, var_20))
    boll_lower = mean_20 - 2.0 * std_20
    boll_bias = ((current_close - boll_lower) / boll_lower) if boll_lower > 1e-4 else 0.0

    # 6. 10 日历史收益波动率在 89 日内的分位数 (Panic Volatility Rank)
    daily_rets = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            daily_rets.append((closes[i] / closes[i - 1]) - 1.0)
        else:
            daily_rets.append(0.0)

    vol10_series = []
    for i in range(10, len(daily_rets) + 1):
        window = daily_rets[i - 10:i]
        m_win = sum(window) / 10.0
        v_win = math.sqrt(max(0.0, sum((x - m_win) ** 2 for x in window) / 10.0))
        vol10_series.append(v_win)

    if len(vol10_series) >= 89:
        vol_89_window = vol10_series[-89:]
        current_vol = vol_89_window[-1]
        panic_vol_rank = sum(1 for v in vol_89_window if current_vol >= v) / float(len(vol_89_window))
    else:
        panic_vol_rank = 0.5  # 天数不足时安全默认中位数

    # 7. 恐慌触轨高胜率反弹触发判定
    is_panic_bounce = (panic_vol_rank >= 0.85) and (current_close <= boll_lower * 1.01)

    # 8. 晨间跳空过滤
    morning_gap = ((today_open / prev_close) - 1.0) if prev_close > 0 else 0.0
    gap_warning = morning_gap > 0.015

    # 浮点极值与 NaN 安全过滤
    def safe_float(v, default=0.0):
        if math.isnan(v) or math.isinf(v):
            return default
        return v

    return {
        "current_close": safe_float(current_close),
        "prev_close": safe_float(prev_close),
        "daily_change_pct": safe_float((current_close / prev_close - 1.0) * 100.0),
        "atr20": safe_float(atr20),
        "risk_adj_mom20": safe_float(risk_adj_mom20),
        "mom_5d_pct": safe_float(mom_5d * 100.0),
        "mom_20d_pct": safe_float(mom_20d * 100.0),
        "accel5_20": safe_float(accel5_20),
        "ma20": safe_float(ma20),
        "above_ma20": bool(above_ma20),
        "boll_lower": safe_float(boll_lower),
        "boll_bias_pct": safe_float(boll_bias * 100.0),
        "panic_vol_rank": safe_float(panic_vol_rank, 0.5),
        "is_panic_bounce": bool(is_panic_bounce),
        "morning_gap_pct": safe_float(morning_gap * 100.0),
        "gap_warning": bool(gap_warning),
    }


def compute_portfolio_signals():
    """
    主计算流程 (P3 兜底增强版):
      1. 获取上证指数 (sh000001)，若失效则强制兜底为防守模式 (50% 红利低波 + 50% 现金)；
      2. 遍历 ETF 标的池，主备双源拉取数据；
      3. 故障隔离机制：停牌、无成交或数据缺失标的自动跳过，不阻断其余标的轮动；
      4. 动态计算等权市场基准与 MA20 大盘 Regime 状态机；
      5. 截面 Z-Score 复合动量标准化，零标准差数值保护；
      6. 输出完整的调仓与资金方案。
    """
    # 1. 优先获取上证指数作为大盘总锚
    sh_index = fetch_shanghai_index()
    sh_available = (sh_index is not None and sh_index.get("price", 0) > 0)

    raw_data = {}
    indicators = {}
    isolated_etfs = []  # 故障隔离记录列表

    # 2. 遍历获取各 ETF 历史与盘中数据
    for code, info in ETF_UNIVERSE.items():
        records, is_valid, status_msg = fetch_etf_history_and_live(info)
        if not is_valid:
            print(f"[Warn] 标的故障隔离: 【{info['name']}】({code}) - 原因: {status_msg}")
            isolated_etfs.append({"code": code, "name": info["name"], "reason": status_msg})
            continue

        fac = calculate_factors_for_etf(records)
        if not fac:
            print(f"[Warn] 标的故障隔离: 【{info['name']}】({code}) - 因子计算失败，已隔离跳过")
            isolated_etfs.append({"code": code, "name": info["name"], "reason": "因子数值计算失败"})
            continue

        raw_data[code] = records
        indicators[code] = fac

    # 3. 校验健康标的数量
    valid_codes = list(indicators.keys())
    if len(valid_codes) < 2:
        print("[Critical] 标的池健康样本不足 2 只，触发终极防守兜底")
        target_etf_1 = "512890" if "512890" in indicators else (valid_codes[0] if valid_codes else "CASH")
        return {
            "as_of_date": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d"),
            "is_bear_regime": True,
            "sh_index": sh_index,
            "ew_current": 1000.0,
            "ew_ma20": 1000.0,
            "ew_ma20_slope": 0.0,
            "ranked_etfs": [],
            "isolated_etfs": isolated_etfs,
            "target_etf_1": target_etf_1,
            "target_etf_2": "CASH",
            "target_reason": "标的池有效样本不足兜底: 切换为保守防御模式 (50% 红利低波 + 50% 现金)",
            "fallback_triggered": True
        }

    # 4. 计算健康标的等权市场基准指数与 MA20
    all_dates = sorted(list(set.intersection(*[set(r["date"] for r in raw_data[c]) for c in valid_codes])))
    if len(all_dates) < 25:
        print("[Warn] 历史公共交易日不足 25 日，沿用最新单日数据")
        all_dates = sorted(list(set.union(*[set(r["date"] for r in raw_data[c]) for c in valid_codes])))

    ew_history = []
    base_prices = {}
    for c in valid_codes:
        p_match = [r["close"] for r in raw_data[c] if r["date"] == all_dates[0]]
        base_prices[c] = p_match[0] if p_match and p_match[0] > 0 else raw_data[c][0]["close"]

    for dt in all_dates:
        norm_sum = 0.0
        counted = 0
        for c in valid_codes:
            p_match = [r["close"] for r in raw_data[c] if r["date"] == dt]
            if p_match and base_prices[c] > 0:
                norm_sum += (p_match[0] / base_prices[c]) * 1000.0
                counted += 1
        if counted > 0:
            ew_history.append({"date": dt, "ew_index": norm_sum / float(counted)})

    ew_closes = [h["ew_index"] for h in ew_history] if ew_history else [1000.0]
    ew_current = ew_closes[-1]
    slice_len = min(len(ew_closes), 20)
    ew_ma20 = sum(ew_closes[-slice_len:]) / float(slice_len)

    if len(ew_closes) >= 25:
        ew_ma20_prev5 = sum(ew_closes[-25:-5]) / 20.0
        ew_ma20_slope = (ew_ma20 - ew_ma20_prev5) / 5.0
        is_below_ma20_today = ew_closes[-1] < ew_ma20
        is_below_ma20_yesterday = ew_closes[-2] < (sum(ew_closes[-21:-1]) / 20.0)
        is_bear_regime = is_below_ma20_today and is_below_ma20_yesterday and (ew_ma20_slope < 0)
    else:
        ew_ma20_slope = 0.0
        is_bear_regime = False

    # 5. P3 大盘基准失效兜底 (Regime Fallback): 若上证指数无法获取，强制进入空头防御模式
    regime_fallback_triggered = False
    if not sh_available:
        print("[Alert] 上证指数主备数据源均无法获取，触发防黑天鹅失控保护 (Regime Fallback) -> 强制进入保守防御模式")
        is_bear_regime = True
        regime_fallback_triggered = True

    # 6. 截面计算 Z-Score 复合动量分 (零标准差与除零全量保护)
    mom_vals = [indicators[c]["risk_adj_mom20"] for c in valid_codes]
    acc_vals = [indicators[c]["accel5_20"] for c in valid_codes]
    n_valid = len(valid_codes)

    m_mean = sum(mom_vals) / float(n_valid)
    m_var = sum((x - m_mean) ** 2 for x in mom_vals) / float(n_valid)
    m_std = math.sqrt(max(0.0, m_var))
    if m_std < 1e-8:
        m_std = 1.0  # 零标准差保护

    a_mean = sum(acc_vals) / float(n_valid)
    a_var = sum((x - a_mean) ** 2 for x in acc_vals) / float(n_valid)
    a_std = math.sqrt(max(0.0, a_var))
    if a_std < 1e-8:
        a_std = 1.0  # 零标准差保护

    scored_list = []
    for code in valid_codes:
        ind = indicators[code]
        z_mom = (ind["risk_adj_mom20"] - m_mean) / m_std
        z_acc = (ind["accel5_20"] - a_mean) / a_std
        composite_score = z_mom + z_acc
        if math.isnan(composite_score) or math.isinf(composite_score):
            composite_score = 0.0

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
            "is_tactical": ind["is_panic_bounce"],
            "gap_warning": ind["gap_warning"],
            "panic_rank": ind["panic_vol_rank"],
            "boll_bias": ind["boll_bias_pct"],
            "is_isolated": False
        })

    # 排序逻辑:
    # 1. 战术反弹标的优先享受置换权
    # 2. 进攻动量标的按复合得分降序排序 (必须在 MA20 上方方可作为进攻候选)
    scored_list.sort(key=lambda x: (x["is_tactical"], x["above_ma20"], x["composite_score"]), reverse=True)

    # 选定 Top-2
    if is_bear_regime:
        # 防守优选红利低波 (512890)，若 512890 自身故障隔离，回退至银行 (512800) 或现金
        if "512890" in indicators:
            target_etf_1 = "512890"
        elif "512800" in indicators:
            target_etf_1 = "512800"
        elif "159611" in indicators:
            target_etf_1 = "159611"
        else:
            target_etf_1 = "CASH"

        target_etf_2 = "CASH"
        if regime_fallback_triggered:
            target_reason = "大盘基准失效兜底: 上证指数无法获取，防范黑天鹅失控，强制回退为保守防御模式 (50% 红利低波 + 50% 现金)"
        else:
            sh_desc = f"(上证: {sh_index['price']:.1f}点, {sh_index['change_pct']:+.2f}%)" if sh_index else ""
            target_reason = f"大盘连续破位空头防守: 50% 配红利低波锁定下行风险，50% 持现金避险 {sh_desc}"
    else:
        target_etf_1 = scored_list[0]["code"]
        target_etf_2 = scored_list[1]["code"] if len(scored_list) > 1 else "CASH"
        sh_desc = f"[上证 {sh_index['price']:.1f}点 {sh_index['change_pct']:+.2f}%]" if sh_index else ""
        target_reason = f"多头进攻优选: 【{scored_list[0]['name']}】(得分 {scored_list[0]['composite_score']:+.2f}) + 【{scored_list[1]['name']}】(得分 {scored_list[1]['composite_score']:+.2f}) {sh_desc}"

    # 将被隔离的标的补充在天梯末尾展示，便于直观审计
    for iso in isolated_etfs:
        scored_list.append({
            "code": iso["code"],
            "name": iso["name"],
            "group": ETF_UNIVERSE[iso["code"]]["group"],
            "price": 0.0,
            "change_pct": 0.0,
            "mom20_pct": 0.0,
            "mom5_pct": 0.0,
            "composite_score": -99.99,
            "above_ma20": False,
            "is_tactical": False,
            "gap_warning": False,
            "panic_rank": 0.0,
            "boll_bias": 0.0,
            "is_isolated": True,
            "isolated_reason": iso["reason"]
        })

    return {
        "as_of_date": all_dates[-1] if all_dates else datetime.now(BEIJING_TZ).strftime("%Y-%m-%d"),
        "is_bear_regime": is_bear_regime,
        "sh_index": sh_index,
        "ew_current": ew_current,
        "ew_ma20": ew_ma20,
        "ew_ma20_slope": ew_ma20_slope,
        "ranked_etfs": scored_list,
        "isolated_etfs": isolated_etfs,
        "target_etf_1": target_etf_1,
        "target_etf_2": target_etf_2,
        "target_reason": target_reason,
        "fallback_triggered": regime_fallback_triggered
    }


def find_optimal_lots(c1, c2, budget):
    """
    精确整数规划求解双标的在整手约束与交易费率下的最优手数 (lots1, lots2)。
    目标优先级:
      1. 双标的有效配置: 若总资金足以各买 1 手 (c1 + c2 <= budget)，则严格保证 lots1 >= 1 且 lots2 >= 1；
      2. 消除零钱闲置 (Cash Drag): 最小化剩余闲置现金 (maximize utilization)；
      3. 均衡持仓: 在同等极低闲置现金下，优先选取两标的总市值最接近 50:50 均衡的组合。
    数值安全兜底: 全面防护负数、零除和越界。
    """
    if budget <= 0 or c1 <= 0 or c2 <= 0:
        return {"x1": 0, "x2": 0, "spent": 0.0, "cash_left": max(0.0, budget), "imbalance": 0.0, "penalty": 0.0}

    can_hold_both = (c1 + c2) <= budget

    if can_hold_both:
        base1 = int((budget * 0.5) // c1)
        # 大资金 (如 100 万) 在 50% 基准附近上下微调搜索，微秒级收敛且必为全局最优
        if budget > 20000:
            search_range = range(max(1, base1 - 15), base1 + 16)
        else:
            search_range = range(1, int(budget // c1) + 1)
    else:
        # 资金不足以同时持有两只各 1 手，降级为持有能买得起的一只或 0
        search_range = range(0, int(budget // c1) + 1)

    best = None
    for x1 in search_range:
        rem = budget - (x1 * c1)
        if rem < -1e-6:
            continue
        x2 = int(rem // c2)
        if can_hold_both and x2 < 1:
            continue
        spent = (x1 * c1) + (x2 * c2)
        cash_left = budget - spent
        imbalance = abs((x1 * c1) - (x2 * c2))
        penalty = cash_left + 0.01 * imbalance
        if best is None or penalty < best["penalty"]:
            best = {
                "x1": x1,
                "x2": x2,
                "spent": spent,
                "cash_left": cash_left,
                "imbalance": imbalance,
                "penalty": penalty
            }

    if best is None:
        return {"x1": 0, "x2": 0, "spent": 0.0, "cash_left": max(0.0, budget), "imbalance": 0.0, "penalty": 0.0}

    return best


def optimize_portfolio_allocation(t1_code, p1, t2_code, p2, budget, fee_rate=0.0001):
    """
    P2 优化项: 背包规划资金拉满算法
    ====================================================================
    目标:
      1. 50% 基准配额平衡
      2. 100 股整手约束下，精确整数规划拉满，消除 Cash Drag
      3. 计算资金利用率与真实交易费用 (万1免五)
      4. 空头模式: 50% 标的 1 + 50% 现金储备 (防守避险)
      5. 多头模式: 双 ETF 各 50% 目标，动态求解全局最优整手
    """
    if budget <= 0:
        return {}

    p1 = max(float(p1), 0.01)

    # 空头防守模式: 50% 红利低波 + 50% 现金
    if t2_code == "CASH" or p2 <= 0:
        c1 = p1 * 100.0 * (1.0 + fee_rate)
        half_budget = budget * 0.5
        lots1 = int(half_budget // c1) if c1 > 0 else 0
        shares1 = lots1 * 100
        cost1 = shares1 * p1
        fee1 = cost1 * fee_rate
        spent1 = cost1 + fee1
        defensive_cash = half_budget
        idle_cash = half_budget - spent1
        total_cash_left = budget - spent1

        return {
            "budget": budget,
            "is_bear": True,
            "t1": {
                "code": t1_code,
                "lots": lots1,
                "shares": shares1,
                "cost": round(cost1, 2),
                "fee": round(fee1, 2),
                "weight_pct": round(spent1 / budget * 100.0, 2)
            },
            "t2": {
                "code": "CASH",
                "lots": 0,
                "shares": 0,
                "cost": round(defensive_cash, 2),
                "fee": 0.0,
                "weight_pct": round(defensive_cash / budget * 100.0, 2)
            },
            "total_spent": round(spent1, 2),
            "defensive_cash": round(defensive_cash, 2),
            "idle_cash": round(idle_cash, 2),
            "cash_left": round(idle_cash, 2),
            "total_cash_left": round(total_cash_left, 2),
            "utilization_pct": round((budget - idle_cash) / budget * 100.0, 2),
            "stock_weight_pct": round(spent1 / budget * 100.0, 2)
        }

    # 多头双标的模式
    p2 = max(float(p2), 0.01)
    c1 = p1 * 100.0 * (1.0 + fee_rate)
    c2 = p2 * 100.0 * (1.0 + fee_rate)

    best = find_optimal_lots(c1, c2, budget)
    lots1 = best["x1"]
    lots2 = best["x2"]

    cost1 = lots1 * 100 * p1
    fee1 = cost1 * fee_rate
    cost2 = lots2 * 100 * p2
    fee2 = cost2 * fee_rate
    total_spent = cost1 + fee1 + cost2 + fee2
    cash_left = budget - total_spent

    return {
        "budget": budget,
        "is_bear": False,
        "t1": {
            "code": t1_code,
            "lots": lots1,
            "shares": lots1 * 100,
            "cost": round(cost1, 2),
            "fee": round(fee1, 2),
            "weight_pct": round((cost1 + fee1) / budget * 100.0, 2)
        },
        "t2": {
            "code": t2_code,
            "lots": lots2,
            "shares": lots2 * 100,
            "cost": round(cost2, 2),
            "fee": round(fee2, 2),
            "weight_pct": round((cost2 + fee2) / budget * 100.0, 2)
        },
        "total_spent": round(total_spent, 2),
        "defensive_cash": 0.0,
        "idle_cash": round(cash_left, 2),
        "cash_left": round(cash_left, 2),
        "total_cash_left": round(cash_left, 2),
        "utilization_pct": round(total_spent / budget * 100.0, 2)
    }


def build_monitor_content(analysis):
    """
    生成针对邮件 (HTML+纯文本)、多通道 Webhook (Markdown+纯文本) 以及 GitHub Actions Step Summary 的完整报告内容
    包含 1,000 元、5,000 元及 1,000,000 元大额实盘方案
    """
    as_of = analysis["as_of_date"]
    is_bear = analysis["is_bear_regime"]
    ranked = analysis["ranked_etfs"]
    isolated = analysis.get("isolated_etfs", [])
    sh_index = analysis.get("sh_index")

    t1_code = analysis["target_etf_1"]
    t2_code = analysis["target_etf_2"]

    t1_name = "现金储备 (防守)" if t1_code == "CASH" else ETF_UNIVERSE[t1_code]["name"]
    t2_name = "现金储备 (防守)" if t2_code == "CASH" else ETF_UNIVERSE[t2_code]["name"]

    p1 = 1.0 if t1_code == "CASH" else ([x["price"] for x in ranked if x["code"] == t1_code and not x.get("is_isolated")] or [1.0])[0]
    p2 = 1.0 if t2_code == "CASH" else ([x["price"] for x in ranked if x["code"] == t2_code and not x.get("is_isolated")] or [1.0])[0]

    # 背包优化整手资金分配 (1K / 5K / 1M)
    alloc_1k = optimize_portfolio_allocation(t1_code, p1, t2_code, p2, 1000.0)
    alloc_5k = optimize_portfolio_allocation(t1_code, p1, t2_code, p2, 5000.0)
    alloc_1m = optimize_portfolio_allocation(t1_code, p1, t2_code, p2, 1000000.0)
    alloc_results = {1000: alloc_1k, 5000: alloc_5k, 1000000: alloc_1m}

    # 状态色板
    if analysis.get("fallback_triggered"):
        status_title = "🛡️ 大盘基准失效兜底 (黑天鹅防失控模式)"
        status_bg = "#fff1f0"
        status_color = "#cf1322"
    elif is_bear:
        status_title = "🟡 空头防守模式 (切换红利低波+现金)"
        status_bg = "#fff8e6"
        status_color = "#b78103"
    else:
        status_title = "🟢 多头进攻模式 (持有最强双龙头)"
        status_bg = "#e6f9f0"
        status_color = "#0e8a16"

    # 大盘基准状态文本
    sh_txt = f"上证指数: {sh_index['price']:.2f} ({sh_index['change_pct']:+.2f}%) [{sh_index['provider']}]" if sh_index else "上证指数: 未获取 (触发失效兜底)"

    # 标的隔离告警 HTML/Markdown
    isolated_alert_html = ""
    isolated_alert_md = ""
    if isolated:
        iso_items = [f"<b>{x['name']}</b> ({x['code']}): {x['reason']}" for x in isolated]
        isolated_alert_html = f"""
        <div style="background:#fff2e8;border:1px solid #ffbb96;border-left:6px solid #fa541c;padding:12px 16px;border-radius:6px;margin-bottom:20px;font-size:13px;color:#d4380d;">
            <b>⚠️ 单标的故障隔离告警</b>: 检测到以下标的存在停牌或数据异常，系统已自动隔离跳过，未影响其余标的动量排序:<br>
            {'<br>'.join(['· ' + it for it in iso_items])}
        </div>
        """
        isolated_alert_md = "\n> ⚠️ **单标的故障隔离告警**: " + " | ".join([f"{x['name']}({x['code']}): {x['reason']}" for x in isolated]) + "\n"

    # 表格行 HTML (天梯榜)
    table_rows_html = ""
    for idx, r in enumerate(ranked, 1):
        if r.get("is_isolated"):
            table_rows_html += f"""
            <tr style="background:#fff1f0;text-align:center;border-bottom:1px solid #e8e8e8;color:#999;">
                <td style="padding:10px 6px;">-</td>
                <td style="padding:10px 6px;text-align:left;"><b>{r['name']}</b> ({r['code']}) <span style="background:#f5222d;color:#fff;padding:2px 6px;border-radius:4px;font-size:11px;">⚠️故障隔离</span></td>
                <td style="padding:10px 6px;">{r['group']}</td>
                <td style="padding:10px 6px;">-</td>
                <td style="padding:10px 6px;">-</td>
                <td style="padding:10px 6px;">-</td>
                <td style="padding:10px 6px;">-</td>
                <td style="padding:10px 6px;color:#cf1322;">{r.get('isolated_reason', '数据异常')}</td>
            </tr>
            """
            continue

        is_picked = r["code"] in [t1_code, t2_code]
        row_bg = "#f0f7ff" if is_picked else ("#ffffff" if idx % 2 == 1 else "#fcfcfc")
        pick_badge = '<span style="background:#1890ff;color:#fff;padding:2px 6px;border-radius:4px;font-size:12px;">入选持仓</span>' if is_picked else ""
        tactical_badge = '<span style="background:#fa8c16;color:#fff;padding:2px 6px;border-radius:4px;font-size:12px;">恐慌反弹</span>' if r["is_tactical"] else ""
        gap_badge = '<span style="background:#ff4d4f;color:#fff;padding:2px 6px;border-radius:4px;font-size:12px;">高开跳空</span>' if r["gap_warning"] else ""

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

    # 资金方案 HTML 生成辅助
    def format_plan_html(a, label, bg="#ffffff"):
        t1_txt = f"买入 {a['t1']['lots']} 手 ({a['t1']['shares']:,}股)<br><span style='font-size:12px;color:#555;'>耗资 {a['t1']['cost']:,.2f}元 ({a['t1']['weight_pct']:.1f}%)</span>"
        if a["is_bear"]:
            t2_txt = f"<span style='color:#b78103;font-weight:bold;'>50% 现金避险</span><br><span style='font-size:12px;color:#555;'>防守持币 {a['defensive_cash']:,.2f}元 ({a['t2']['weight_pct']:.1f}%)</span>"
            spare_txt = f"{a['idle_cash']:,.2f} 元<br><span style='font-size:11px;color:#888;'>(零钱闲置)</span>"
        else:
            t2_txt = f"买入 {a['t2']['lots']} 手 ({a['t2']['shares']:,}股)<br><span style='font-size:12px;color:#555;'>耗资 {a['t2']['cost']:,.2f}元 ({a['t2']['weight_pct']:.1f}%)</span>"
            spare_txt = f"{a['cash_left']:,.2f} 元"

        return f"""
        <tr style="text-align:center;border-bottom:1px solid #e8e8e8;background:{bg};">
            <td style="padding:10px;font-weight:bold;">{label}</td>
            <td style="padding:10px;color:#1890ff;font-weight:bold;">{t1_txt}</td>
            <td style="padding:10px;color:#1890ff;font-weight:bold;">{t2_txt}</td>
            <td style="padding:10px;color:#52c41a;font-weight:bold;">{spare_txt}</td>
            <td style="padding:10px;font-weight:bold;color:#096dd9;">{a['utilization_pct']:.2f}%</td>
        </tr>
        """

    plan_rows_html = (
        format_plan_html(alloc_1k, "1,000 元<br><span style='font-size:11px;color:#888;'>(极小微整手)</span>", "#fffbe6") +
        format_plan_html(alloc_5k, "5,000 元<br><span style='font-size:11px;color:#888;'>(常规小微实盘)</span>", "#ffffff") +
        format_plan_html(alloc_1m, "1,000,000 元<br><span style='font-size:11px;color:#888;'>(大额模拟实盘)</span>", "#f6ffed")
    )

    # 完整 HTML 邮件内容
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family:'PingFang SC','Microsoft YaHei',Arial,sans-serif;background-color:#f4f6f9;margin:0;padding:20px;color:#333;">
        <div style="max-width:820px;margin:0 auto;background:#fff;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,0.06);overflow:hidden;border:1px solid #e6ebf1;">
            
            <!-- 头部 Banner -->
            <div style="background:linear-gradient(135deg, #1890ff 0%, #0050b3 100%);padding:24px 30px;color:#fff;">
                <div style="font-size:24px;font-weight:bold;letter-spacing:1px;">🚀 A股免五 ETF 高胜率双标的轮动内参</div>
                <div style="font-size:14px;opacity:0.85;margin-top:6px;">盘中 14:30 自动监测 | 主备数据源 & 故障隔离 & 背包资金拉满 & 100万大额风控</div>
            </div>

            <div style="padding:24px 30px;">
                {isolated_alert_html}

                <!-- 核心状态卡片 -->
                <div style="background:{status_bg};border-left:6px solid {status_color};padding:16px 20px;border-radius:6px;margin-bottom:24px;">
                    <div style="font-size:18px;font-weight:bold;color:{status_color};">{status_title}</div>
                    <div style="font-size:14px;color:#555;margin-top:6px;line-height:1.6;">
                        <b>判定理由</b>: {analysis['target_reason']}<br>
                        <b>大盘基准锚</b>: {sh_txt} | <b>等权基准</b>: {analysis['ew_current']:.1f}点 (MA20: {analysis['ew_ma20']:.1f}点, 斜率: {analysis['ew_ma20_slope']:+.2f})
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

                <!-- 实盘背包整手配置速查表 (含 100 万方案) -->
                <h3 style="border-bottom:2px solid #1890ff;padding-bottom:8px;color:#1f1f1f;">💰 背包规划资金拉满建仓方案 (免五万1，消除零钱闲置)</h3>
                <table style="width:100%;border-collapse:collapse;margin-bottom:16px;font-size:13px;">
                    <tr style="background:#fafafa;text-align:center;border-bottom:1px solid #e8e8e8;font-weight:bold;">
                        <th style="padding:10px 8px;">账户资金</th>
                        <th style="padding:10px 8px;">标的一: {t1_name}</th>
                        <th style="padding:10px 8px;">标的二: {t2_name}</th>
                        <th style="padding:10px 8px;">备用现金</th>
                        <th style="padding:10px 8px;">资金利用率</th>
                    </tr>
                    {plan_rows_html}
                </table>

                <!-- 100 万元大额专项冲击成本与流动性指引 -->
                <div style="background:#fff7e6;border:1px solid #ffd591;border-left:6px solid #fa8c16;padding:14px 18px;border-radius:6px;margin-bottom:24px;line-height:1.7;font-size:13px;color:#873800;">
                    <div style="font-size:15px;font-weight:bold;margin-bottom:6px;color:#d4380d;">⚠️ 100 万元大额模拟实盘·交易执行与冲击成本关键指引</div>
                    1. <b>严禁市价单委托</b>: 单标的约 50 万元（约 1,500 ~ 5,000 手），若使用市价单直接吃单，在中小盘 ETF 上极易瞬间击穿 3~5 档深度，造成 0.3%~0.8% 的非必要冲击滑点（单笔损失可达 1,500~4,000 元）。<br>
                    2. <b>限价分批委托 (TWAP 思想)</b>: 14:30 收到内参后，建议在 14:35 ~ 14:55 时间窗口内，分 3~5 笔限价挂买一/卖一档委托（每笔 300~500 手），被动吃单降低摩擦。<br>
                    3. <b>盘口流动性门槛</b>: 重点观察标的当日成交额（建议 > 5,000 万元为佳）。未完全成交部分可在 14:55 撤单重挂，或在 14:57 以前参与尾盘集合竞价完成最终建仓。
                </div>

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
                A股免五 ETF 高胜率双标的轮动系统 (P1+P2+P3兜底版) | GitHub Actions 每日 14:30 自动生成 | 数据时间: {as_of}
            </div>
        </div>
    </body>
    </html>
    """

    # 纯文本建仓描述辅助
    if is_bear:
        t2_txt_1k = f"防守持币 {alloc_1k['defensive_cash']:.2f} 元 ({alloc_1k['t2']['weight_pct']:.1f}%)"
        t2_txt_5k = f"防守持币 {alloc_5k['defensive_cash']:.2f} 元 ({alloc_5k['t2']['weight_pct']:.1f}%)"
        t2_txt_1m = f"防守持币 {alloc_1m['defensive_cash']:,.2f} 元 ({alloc_1m['t2']['weight_pct']:.1f}%)"
        spare_lbl = "零钱闲置"
    else:
        t2_txt_1k = f"买入 {alloc_1k['t2']['lots']} 手 ({alloc_1k['t2']['shares']} 股), 耗资 {alloc_1k['t2']['cost']:.2f} 元"
        t2_txt_5k = f"买入 {alloc_5k['t2']['lots']} 手 ({alloc_5k['t2']['shares']} 股), 耗资 {alloc_5k['t2']['cost']:.2f} 元"
        t2_txt_1m = f"买入 {alloc_1m['t2']['lots']} 手 ({alloc_1m['t2']['shares']:,} 股), 耗资 {alloc_1m['t2']['cost']:,.2f} 元"
        spare_lbl = "留存备用金"

    # 纯文本内容
    text_lines = [
        f"【A股免五ETF高胜率双标的轮动内参】({as_of})",
        "=" * 60,
        f"状态: {status_title}",
        f"理由: {analysis['target_reason']}",
        f"大盘: {sh_txt}",
        "-" * 60,
    ]
    if isolated:
        text_lines.append("【标的隔离告警】")
        for x in isolated:
            text_lines.append(f" - {x['name']} ({x['code']}): {x['reason']}")
        text_lines.append("-" * 60)

    text_lines.extend([
        "【今日建议目标持仓 (各 50% 仓位)】",
        f"标的 1: {t1_name} ({t1_code}) - 参考价: {p1:.3f} 元",
        f"标的 2: {t2_name} ({t2_code}) - 参考价: {p2:.3f} 元",
        "-" * 60,
        "【背包规划资金拉满建仓方案】",
        f"1. 1,000 元极小本金方案:",
        f"   - {t1_name}: 买入 {alloc_1k['t1']['lots']} 手 ({alloc_1k['t1']['shares']} 股), 耗资 {alloc_1k['t1']['cost']:.2f} 元",
        f"   - {t2_name}: {t2_txt_1k}",
        f"   - {spare_lbl}: {alloc_1k['cash_left']:.2f} 元 | 资金规划率: {alloc_1k['utilization_pct']:.2f}%",
        f"2. 5,000 元常规实盘方案:",
        f"   - {t1_name}: 买入 {alloc_5k['t1']['lots']} 手 ({alloc_5k['t1']['shares']} 股), 耗资 {alloc_5k['t1']['cost']:.2f} 元",
        f"   - {t2_name}: {t2_txt_5k}",
        f"   - {spare_lbl}: {alloc_5k['cash_left']:.2f} 元 | 资金规划率: {alloc_5k['utilization_pct']:.2f}%",
        f"3. 1,000,000 元大额模拟实盘方案:",
        f"   - {t1_name}: 买入 {alloc_1m['t1']['lots']} 手 ({alloc_1m['t1']['shares']:,} 股), 耗资 {alloc_1m['t1']['cost']:,.2f} 元",
        f"   - {t2_name}: {t2_txt_1m}",
        f"   - {spare_lbl}: {alloc_1m['cash_left']:,.2f} 元 | 资金规划率: {alloc_1m['utilization_pct']:.2f}%",
        f"   * 100万冲击提示: 严禁市价单！请于 14:35~14:55 分 3-5 笔限价挂单委托，规避冲击滑点。",
        "=" * 60,
        "【全天梯动量排名 Top 5】",
    ])
    for idx, r in enumerate([x for x in ranked if not x.get("is_isolated")][:5], 1):
        text_lines.append(f"{idx}. {r['name']}({r['code']}): 现价 {r['price']:.3f}, 复合得分 {r['composite_score']:+.2f}, 今日涨跌 {r['change_pct']:+.2f}%")

    text = "\n".join(text_lines)
    subject = f"【ETF轮动内参】{as_of} 盘中指引: {'防守' if is_bear else '多头优选'} [{t1_name}+{t2_name}]"

    # Markdown 内容 (专供企业微信 & 钉钉机器人)
    top5_md = "\n".join([
        f"> {i}. **{r['name']}** ({r['code']}): 现价 `{r['price']:.3f}`, 得分 `{r['composite_score']:+.2f}`, 今日 `{r['change_pct']:+.2f}%`"
        for i, r in enumerate([x for x in ranked if not x.get("is_isolated")][:5], 1)
    ])

    t2_md_1k = f"防守持币 {alloc_1k['defensive_cash']:.1f}元" if t2_code == "CASH" else f"买入 {alloc_1k['t2']['lots']} 手 ({alloc_1k['t2']['shares']} 股, {alloc_1k['t2']['cost']:.1f}元)"
    t2_md_5k = f"防守持币 {alloc_5k['defensive_cash']:.1f}元" if t2_code == "CASH" else f"买入 {alloc_5k['t2']['lots']} 手 ({alloc_5k['t2']['shares']} 股, {alloc_5k['t2']['cost']:.1f}元)"
    t2_md_1m = f"防守持币 {alloc_1m['defensive_cash']:,.1f}元" if t2_code == "CASH" else f"买入 {alloc_1m['t2']['lots']} 手 ({alloc_1m['t2']['shares']:,} 股, {alloc_1m['t2']['cost']:,.1f}元)"

    webhook_markdown = f"""### 🚀 A股免五 ETF 轮动内参 ({as_of})
> **市场状态**: {status_title}
> **核心理由**: {analysis['target_reason']}
> **大盘基准**: {sh_txt}
{isolated_alert_md}
🎯 **今日建议持仓 (各 50% 仓位)**:
- **标的 1**: {t1_name} (`{t1_code}`) | 现价: `{p1:.3f}` 元
- **标的 2**: {t2_name} (`{t2_code}`) | 现价: `{p2:.3f}` 元

💰 **整手背包规划资金拉满方案 (免五万1)**:
- **1,000 元方案**:
  · {t1_name}: 买入 {alloc_1k['t1']['lots']} 手 ({alloc_1k['t1']['shares']} 股, {alloc_1k['t1']['cost']:.1f}元)
  · {t2_name}: {t2_md_1k}
  · 零钱闲置: `{alloc_1k['cash_left']:.2f}` 元 | 规划率: `{alloc_1k['utilization_pct']:.1f}%`
- **5,000 元方案**:
  · {t1_name}: 买入 {alloc_5k['t1']['lots']} 手 ({alloc_5k['t1']['shares']} 股, {alloc_5k['t1']['cost']:.1f}元)
  · {t2_name}: {t2_md_5k}
  · 零钱闲置: `{alloc_5k['cash_left']:.2f}` 元 | 规划率: `{alloc_5k['utilization_pct']:.1f}%`
- **1,000,000 元大额方案**:
  · {t1_name}: 买入 {alloc_1m['t1']['lots']} 手 ({alloc_1m['t1']['shares']:,} 股, {alloc_1m['t1']['cost']:,.1f}元)
  · {t2_name}: {t2_md_1m}
  · 零钱闲置: `{alloc_1m['cash_left']:,.2f}` 元 | 规划率: `{alloc_1m['utilization_pct']:.2f}%`

⚠️ **100万大额执行提醒**: 严禁市价单！请于 14:35~14:55 分 3-5 笔限价挂买一/卖一档委托，规避 0.3%~0.8% 的盘口冲击滑点。

📊 **Top 5 动量标的**:
{top5_md}
"""

    # P3 优化项: GitHub Actions Step Summary 专属 Markdown (超全表格与图表排版)
    # 生成全量标的天梯 Markdown 表格
    step_table_lines = [
        "| 排名 | ETF 标的 | 分类 | 最新价 | 今日涨跌 | 20日涨跌 | 复合得分 | 状态 |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |"
    ]
    for idx, r in enumerate(ranked, 1):
        if r.get("is_isolated"):
            step_table_lines.append(f"| - | **{r['name']}** (`{r['code']}`) | {r['group']} | - | - | - | - | ⚠️ `{r.get('isolated_reason', '故障隔离')}` |")
        else:
            is_picked = r["code"] in [t1_code, t2_code]
            pick_tag = " 🌟 **入选**" if is_picked else ""
            tac_tag = " ⚡恐慌反弹" if r["is_tactical"] else ""
            gap_tag = " 🚨高开" if r["gap_warning"] else ""
            status_desc = "✅ 站上MA20" if r["above_ma20"] else "❌ 跌破MA20"
            step_table_lines.append(
                f"| {idx} | **{r['name']}** (`{r['code']}`){pick_tag}{tac_tag}{gap_tag} | {r['group']} | "
                f"`{r['price']:.3f}` | `{r['change_pct']:+.2f}%` | `{r['mom20_pct']:+.1f}%` | "
                f"**`{r['composite_score']:+.2f}`** | {status_desc} |"
            )

    step_table_md = "\n".join(step_table_lines)

    step_summary_md = f"""# 🚀 A股免五 ETF 高胜率双标的轮动内参 ({as_of})

> **市场状态**: **{status_title}**  
> **核心理由**: {analysis['target_reason']}  
> **大盘基准**: `{sh_txt}` | **等权基准**: `{analysis['ew_current']:.1f}` (MA20: `{analysis['ew_ma20']:.1f}`, 斜率: `{analysis['ew_ma20_slope']:+.2f}`)  
{isolated_alert_md}

---

### 🎯 今日 14:50 建议目标持仓 (各 50% 仓位)

| 配置维度 | 标的 1 (50% 仓位) | 标的 2 (50% 仓位) |
| :--- | :--- | :--- |
| **资产名称** | **{t1_name}** (`{t1_code}`) | **{t2_name}** (`{t2_code}`) |
| **参考现价** | `{p1:.3f} 元` | `{p2:.3f} 元` |
| **仓位定位** | 龙头进攻优选 | 龙头进攻优选 / 50% 现金防守避险 |

---

### 💰 背包规划资金拉满建仓方案 (免五万1，消除零钱闲置)

| 资金档位 | 标的一: {t1_name} | 标的二: {t2_name} | 留存备用现金 | 资金规划率 |
| :---: | :--- | :--- | :---: | :---: |
| **1,000 元** *(极小微整手)* | 买入 **{alloc_1k['t1']['lots']}** 手 (`{alloc_1k['t1']['shares']:,}`股, `{alloc_1k['t1']['cost']:.2f}`元) | {t2_md_1k} | `{alloc_1k['cash_left']:.2f}` 元 | **`{alloc_1k['utilization_pct']:.2f}%`** |
| **5,000 元** *(常规小微实盘)* | 买入 **{alloc_5k['t1']['lots']}** 手 (`{alloc_5k['t1']['shares']:,}`股, `{alloc_5k['t1']['cost']:.2f}`元) | {t2_md_5k} | `{alloc_5k['cash_left']:.2f}` 元 | **`{alloc_5k['utilization_pct']:.2f}%`** |
| **1,000,000 元** *(大额模拟实盘)* | 买入 **{alloc_1m['t1']['lots']}** 手 (`{alloc_1m['t1']['shares']:,}`股, `{alloc_1m['t1']['cost']:,.2f}`元) | {t2_md_1m} | `{alloc_1m['cash_left']:,.2f}` 元 | **`{alloc_1m['utilization_pct']:.2f}%`** |

> ⚠️ **100 万元大额专项冲击成本指引**:
> 1. **严禁市价单委托**: 单标的约 50 万元（1,500 ~ 5,000 手），市价单直接吃单在中小盘 ETF 上极易击穿 3~5 档深度，造成 0.3%~0.8% 冲击滑点（单笔损失 1,500~4,000 元）；
> 2. **分批挂单 (TWAP)**: 14:35 ~ 14:55 窗口内，分 3~5 笔限价挂买一/卖一档委托（每笔 300~500 手）；
> 3. **流动性门槛**: 优先选取当日成交额 > 5,000 万元标的，未完全成交部分在 14:57 以前参与尾盘集合竞价。

---

### 📊 10 只免五核心 ETF 截面动量与信号天梯

{step_table_md}

---

### 💡 交易纪律与执行守则
1. **阈值防抽搐**: 若您当前已持有前 2 名之一，且新标的分差未超过 15%，**坚决不调仓**，避免横跳摩擦；
2. **硬止损与动态止盈**: 单只持仓亏损达到 **-4.0%** 坚决止损转入现金或红利低波；浮盈达 **+7.0%** 后自最高点回撤 2.5% 动态止盈；
3. **尾盘执行**: 建议在 14:50 ~ 14:55 以买一/卖一档限价单委托，规避市价单冲击。

*系统说明: A股免五 ETF 双标的轮动系统 (P1+P2+P3全兜底版) | 主备数据源 + 标的隔离 + Regime兜底 + Step Summary | 生成时间: {datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")}*
"""

    return text, html, webhook_markdown, step_summary_md, subject, alloc_results


def write_github_step_summary(summary_markdown):
    """
    P3 优化项: 推送与展示双重兜底 (Step Summary Fallback)
    将格式化 Markdown 内参自动写入 GitHub Actions 的 $GITHUB_STEP_SUMMARY。
    即便 SMTP 或 Webhook 遭遇网络故障，用户登录 GitHub Actions 页面也能在 1 秒内看到当天完整的图表内参。
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        # 非 GitHub Actions 环境下，静默跳过或调试输出
        return False

    try:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(summary_markdown + "\n")
        print(f"✅ 成功写入 GitHub Actions Step Summary ({summary_path})")
        return True
    except Exception as e:
        print(f"[Warn] 写入 GITHUB_STEP_SUMMARY 失败: {e}")
        return False


def send_email(text_body, html_body, subject):
    """通过 QQ 邮箱 SMTP SSL 发送邮件"""
    user = os.environ.get("SMTP_USER", "").strip()
    code = os.environ.get("SMTP_CODE", "").strip()
    to = os.environ.get("SMTP_TO", "").strip() or user

    if not user or not code:
        print("[Info] 未配置 SMTP_USER / SMTP_CODE 环境变量，跳过邮件发送")
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
        print("💡 提示: QQ 邮箱授权码可能已失效，请前往 QQ 邮箱 -> 设置 -> 账户 -> 生成新的 16 位授权码并在 Secrets 中更新。")
        return False
    except Exception as e:
        print(f"❌ 邮件发送失败: {type(e).__name__}: {e}")
        return False


def validate_webhook_response(platform_name, resp_text):
    """校验 Webhook 响应报文中的业务级状态码，避免静默虚标成功"""
    try:
        data = json.loads(resp_text)
        # 企业微信与钉钉: errcode == 0
        if "errcode" in data:
            if data["errcode"] == 0:
                print(f"✅ {platform_name} Webhook 推送成功")
                return True
            else:
                print(f"❌ {platform_name} Webhook 业务失败 (errcode={data['errcode']}): {data.get('errmsg', '')}")
                return False
        # 飞书机器人: code == 0
        if "code" in data:
            if data["code"] in (0, 200):
                print(f"✅ {platform_name} Webhook 推送成功")
                return True
            else:
                print(f"❌ {platform_name} Webhook 业务失败 (code={data['code']}): {data.get('msg', '')}")
                return False
        # PushPlus 微信推送: code == 200
        if data.get("code") == 200 or data.get("status") == 200:
            print(f"✅ {platform_name} 微信推送成功: {data.get('msg', '请求成功')}")
            return True
        print(f"ℹ️ {platform_name} Webhook 响应: {resp_text[:100]}")
        return True
    except Exception:
        print(f"ℹ️ {platform_name} Webhook 响应: {resp_text[:100]}")
        return True


def send_webhook_notifications(subject, markdown_text, plain_text, html_body):
    """
    P1 优化项: 多通道 Webhook 即时推送
    支持可选配置:
      1. 企业微信机器人: WECOM_WEBHOOK
      2. 钉钉机器人:     DINGTALK_WEBHOOK
      3. 飞书机器人:     FEISHU_WEBHOOK
      4. PushPlus (微信): PUSHPLUS_TOKEN
    未配置则静默跳过，互不影响。
    """
    wecom_url = os.environ.get("WECOM_WEBHOOK", "").strip()
    dingtalk_url = os.environ.get("DINGTALK_WEBHOOK", "").strip()
    feishu_url = os.environ.get("FEISHU_WEBHOOK", "").strip()
    pushplus_token = os.environ.get("PUSHPLUS_TOKEN", "").strip()

    has_any = any([wecom_url, dingtalk_url, feishu_url, pushplus_token])
    if not has_any:
        print("[Info] 未配置 Webhook 环境变量 (WECOM_WEBHOOK / DINGTALK_WEBHOOK / FEISHU_WEBHOOK / PUSHPLUS_TOKEN)，跳过 Webhook 即时推送")
        return

    # 1. 企业微信机器人
    if wecom_url:
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": markdown_text
                }
            }
            resp = http_post_json(wecom_url, payload)
            validate_webhook_response("企业微信", resp)
        except Exception as e:
            print(f"❌ 企业微信 Webhook 推送失败: {e}")

    # 2. 钉钉机器人
    if dingtalk_url:
        try:
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": subject,
                    "text": markdown_text
                }
            }
            resp = http_post_json(dingtalk_url, payload)
            validate_webhook_response("钉钉", resp)
        except Exception as e:
            print(f"❌ 钉钉 Webhook 推送失败: {e}")

    # 3. 飞书机器人
    if feishu_url:
        try:
            payload = {
                "msg_type": "text",
                "content": {
                    "text": f"{subject}\n\n{plain_text}"
                }
            }
            resp = http_post_json(feishu_url, payload)
            validate_webhook_response("飞书", resp)
        except Exception as e:
            print(f"❌ 飞书 Webhook 推送失败: {e}")

    # 4. PushPlus 微信推送
    if pushplus_token:
        try:
            url = "http://www.pushplus.plus/send"
            payload = {
                "token": pushplus_token,
                "title": subject,
                "content": html_body,
                "template": "html"
            }
            resp = http_post_json(url, payload)
            validate_webhook_response("PushPlus", resp)
        except Exception as e:
            print(f"❌ PushPlus 微信推送失败: {e}")


def main():
    force_mode = ("--force" in sys.argv) or (os.environ.get("FORCE_RUN", "").strip().lower() in ("true", "1", "yes"))

    print("=" * 65)
    print("启动 A股免五 ETF 高胜率双标的阈值轮动盘中监控系统 (P1+P2+P3全兜底版)...")
    print("=" * 65)

    # P1 核心特性: 交易日历过滤
    is_trading, reason = check_trading_day(force=force_mode)
    if not is_trading:
        print(f"\n[Info] {reason}")
        print("[Info] 法定节假日或周末休市，系统优雅退出，不发起数据抓取或推送。")
        print("=" * 65)
        return 0

    if force_mode:
        print(f"[Notice] 当前为强制运行模式: {reason}")
    else:
        print(f"[Info] 交易日判定通过: {reason}")

    # 计算信号 (P3 增强版)
    analysis = compute_portfolio_signals()
    text, html, webhook_markdown, step_summary_md, subject, alloc_results = build_monitor_content(analysis)

    print("\n" + text + "\n")

    # P3 核心特性: 推送与展示双重兜底 (写入 GitHub Actions Step Summary)
    write_github_step_summary(step_summary_md)

    # 邮件推送
    send_email(text, html, subject)

    # P1 核心特性: 多通道 Webhook 推送
    send_webhook_notifications(subject, webhook_markdown, text, html)

    print("=" * 65)
    print(">> 盘中监控与决策方案生成完毕！")
    print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
