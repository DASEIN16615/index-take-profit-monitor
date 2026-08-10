# -*- coding: utf-8 -*-
"""
指数监控 v2.0 — 估值×趋势 双轴,仓位调节器,非买卖开关
====================================================
监控:纳指100 / 标普500 / 沪深300 / 中证500

架构(三层分离):
  ① 估值层 -> 新资金配置比例(定投节奏)
     评分 = Σ w_i × 分位_i,分位越高越贵
     - 纳指100: PE分位(weight 1.0)
     - 标普500: PE 0.4 + PB 0.3 + (1-股息率分位) 0.3
     - A股:     PE 0.5 + PB 0.5
     评分 -> 定投比例: <40%→100% | 40-60→75% | 60-75→50% | 75-85→25% | ≥85→0%
  ② 趋势层 -> 存量止盈(严格)
     止盈触发: 评分≥95% 或 (评分≥90% 且 趋势转弱)
     趋势转弱: 收盘价连续≥2日低于SMA200 且 SMA200二十日斜率为负
  ③ 盈利增长 -> 修正显示(不直接进评分,数据可得时展示)
     标普:EPS同比(multpl);纳指/A股: 数据源不可得,标注N/A

数据源:
  美股价格/趋势 : Yahoo chart API (SMA200 自算)
  纳指估值      : worldperatio (PE + 10Y分位 + 20Y参考)
  标普估值      : multpl (PE/PB/股息率/EPS, 近10年分位)
  A股价格/趋势 : 新浪日线接口
  A股估值      : 蛋卷基金 (PE/PB分位 + 当前股息率)
"""
import html as html_mod
import json
import math
import os
import re
import smtplib
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.header import Header
from email.mime.text import MIMEText

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CN_TZ = timezone(timedelta(hours=8))
US_TZ_EDT = timezone(timedelta(hours=-4))

# ---------- 配置 ----------
INDICES = {
    "纳指100": {
        "type": "us", "yahoo": "^NDX", "sina": None,
        "pe_src": "worldperatio", "wp_slug": "nasdaq-100",
        "danjuan_name": None, "cost": None,
    },
    "标普500": {
        "type": "us", "yahoo": "^GSPC", "sina": None,
        "pe_src": "multpl", "wp_slug": None,
        "danjuan_name": "标普500", "cost": None,
    },
    "沪深300": {
        "type": "cn", "yahoo": None, "sina": "sh000300",
        "pe_src": None, "wp_slug": None,
        "danjuan_name": "沪深300", "cost": None,
    },
    "中证500": {
        "type": "cn", "yahoo": None, "sina": "sh000905",
        "pe_src": None, "wp_slug": None,
        "danjuan_name": "中证500", "cost": None,
    },
}

# 评分权重(分位越高越贵;缺失指标自动归一化)
WEIGHTS = {
    "纳指100": [("pe", 1.0)],
    "标普500": [("pe", 0.4), ("pb", 0.3), ("dy", 0.3)],  # dy 用 (1-dy_pct)
    "沪深300": [("pe", 0.5), ("pb", 0.5)],
    "中证500": [("pe", 0.5), ("pb", 0.5)],
}

# 定投比例档位 (score 0-1)
DCA_TIERS = [(0.85, 0.0), (0.75, 0.25), (0.60, 0.50), (0.40, 0.75), (0.0, 1.0)]

# 止盈阈值
TP_SCORE = 0.95      # 评分>=95% 直接触发
TP_SCORE_TREND = 0.90  # 评分>=90% 且趋势转弱 触发
TREND_BREAK_DAYS = 2  # 连续低于SMA200天数
SMA200_SLOPE_DAYS = 20

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
VERBOSE = "--verbose" in sys.argv


def http_get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def log(msg):
    if VERBOSE:
        print("[debug]", msg)


def pct_from_z(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def fmt(v, nd=2):
    if v is None:
        return "--"
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return str(v)


# ========== 美股价格 + 趋势 (Yahoo) ==========
def fetch_us_price_trend(symbol):
    """返回 (price, dt_cn, stale, trend_dict)"""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + "?interval=1d&range=1y")
    d = json.loads(http_get(url))
    res = d["chart"]["result"][0]
    meta = res["meta"]
    price = float(meta["regularMarketPrice"])
    dt_cn = datetime.fromtimestamp(int(meta["regularMarketTime"]), tz=US_TZ_EDT).astimezone(CN_TZ)
    stale = (datetime.now(CN_TZ) - dt_cn) > timedelta(days=4)
    closes = [c for c in (res.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose") or [])
              if c is not None]
    trend = calc_trend(closes) if len(closes) >= 220 else None
    return price, dt_cn, stale, trend


# ========== A股价格 + 趋势 (新浪日线) ==========
def fetch_sina_trend(symbol):
    """返回 (price, chg_pct, trend_dict) — 新浪日线 260 天"""
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_=/CN_MarketDataService.getKLineData"
           f"?symbol={symbol}&scale=240&ma=no&datalen=260")
    raw = http_get(url)
    txt = raw.decode("gbk", "ignore")
    m = re.search(r"\((\[.*\])\)", txt, re.S)
    if not m:
        raise ValueError("sina: 解析失败")
    data = json.loads(m.group(1))
    closes = [float(x["close"]) for x in data]
    price = closes[-1]
    prev_close = closes[-2] if len(closes) > 1 else price
    chg = (price / prev_close - 1) * 100 if prev_close else 0.0
    trend = calc_trend(closes) if len(closes) >= 220 else None
    return price, chg, trend


def calc_trend(closes):
    """计算趋势: 逐日 SMA200, 斜率, 连续破位天数
    修复(v3): below_days 用【当天自己的 SMA200】对比,而非今天的 SMA200
    返回 {sma200, slope_pct, below_days, status}
    status: 多头/转弱/空头"""
    n = len(closes)
    if n < 220:
        return None
    # 逐日 SMA200 序列: sma[i] = mean(closes[i-199..i])
    sma = [None] * n
    run = sum(closes[:200])
    sma[199] = run / 200
    for i in range(200, n):
        run += closes[i] - closes[i - 200]
        sma[i] = run / 200
    sma_now = sma[-1]
    # 斜率: 当前 SMA200 vs 20 天前的 SMA200
    if n >= 200 + SMA200_SLOPE_DAYS:
        sma_prev = sma[-1 - SMA200_SLOPE_DAYS]
    else:
        sma_prev = sma_now
    slope = (sma_now - sma_prev) / sma_prev * 100 if sma_prev else 0.0
    # 连续破位: 从最新往回, 当天收盘 < 当天 SMA200
    below_days = 0
    for i in range(n - 1, 199, -1):
        if closes[i] < sma[i]:
            below_days += 1
        else:
            break
    last_close = closes[-1]
    if last_close > sma_now and slope >= 0:
        status = "多头"
    elif last_close < sma_now and slope < 0:
        status = "空头"
    else:
        status = "转弱"
    return {"sma200": sma_now, "slope_pct": slope, "below_days": below_days, "status": status}


# ========== 纳指估值 (worldperatio) ==========
def fetch_worldperatio(slug):
    """返回 {pe, date, pct10, pct20, mu10, sigma10}"""
    url = f"https://worldperatio.com/index/{slug}/"
    page = http_get(url).decode("utf-8", "ignore")

    def block(win):
        m = re.search(
            r'<font class="w3-text-black">([^<]+)</font> · P/E Ratio: <b[^>]*>([\d.]+)</b>\s*·\s*'
            + win + r' Average: <b[^>]*>([\d.]+)</b>\s*·\s*1 Std Dev range: <b[^>]*>\[([\d.]+) , ([\d.]+)\]</b>',
            page,
        )
        if not m:
            return None
        return m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(5)) - float(m.group(3))

    b10, b20 = block("10Y"), block("20Y")
    if not b10 and not b20:
        raise ValueError("worldperatio: 未找到快照")
    b = b10 or b20
    pe = b[1]
    pct10 = pct_from_z((pe - b10[2]) / b10[3]) if b10 else None
    pct20 = pct_from_z((pe - b20[2]) / b20[3]) if b20 else None
    return {"pe": pe, "date": b[0], "pct10": pct10, "pct20": pct20,
            "mu10": b10[2] if b10 else None, "sigma10": b10[3] if b10 else None}


# ========== 标普估值 (multpl 通用表) ==========
def fetch_multpl_full(slug, label):
    """解析 multpl 历史表(by-month/by-year),返回 [(datetime, value), ...] 升序"""
    page = http_get(f"https://www.multpl.com/{slug}/table/by-month").decode("utf-8", "ignore")
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S)
    series = []
    for r in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if len(cells) != 2:
            continue
        d_s = re.sub(r"<[^>]+>", "", cells[0]).strip()
        v_s = re.sub(r"<[^>]+>", "", cells[1]).strip()
        v_s = html_mod.unescape(v_s)
        v_s = re.sub(r"[^\d.\-]", "", v_s)
        try:
            dt = datetime.strptime(d_s, "%b %d, %Y")
            val = float(v_s)
        except Exception:
            continue
        series.append((dt, val))
    if not series:
        raise ValueError(f"multpl {label}: 无数据")
    series.sort()
    return series


def fetch_multpl_series(slug, label):
    """返回 (date_s, value, pct_10y, n_samples)"""
    series = fetch_multpl_full(slug, label)
    cur_dt, cur = series[-1]
    cutoff = series[-1][0] - timedelta(days=365 * 10)
    window = [v for d, v in series if d >= cutoff and v < 1e5]
    if not window:
        window = [v for d, v in series if v < 1e5]
    pct = sum(1 for v in window if v <= cur) / len(window) if window else None
    return cur_dt.strftime("%Y-%m-%d"), cur, pct, len(window)


# ========== A股估值 (蛋卷) ==========
def fetch_danjuan():
    out = {}
    try:
        url = "https://danjuanfunds.com/djapi/index_eva/dj"
        d = json.loads(http_get(url, headers={"Referer": "https://danjuanfunds.com/rn/value-center"}))
        for it in d.get("data", {}).get("items", []):
            y = it.get("yeild")
            out[it.get("name")] = {
                "pe": it.get("pe"), "pe_pct": it.get("pe_percentile"),
                "pb": it.get("pb"), "pb_pct": it.get("pb_percentile"),
                "dy": (y * 100 if isinstance(y, (int, float)) else None),  # 蛋卷为小数,转百分数
                "date": it.get("date"),
            }
    except Exception as e:
        log(f"蛋卷失败: {e}")
    return out


# ========== 评分与信号 ==========
def sanitize_metrics(metrics):
    """数据合理性校验: 分位越界/异常 -> 剔除, 不参与评分(避免网页解析异常产生假信号)"""
    out = {}
    for k, pct in metrics.items():
        if pct is None:
            continue
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            log(f"分位非数值 {k}={pct!r}, 剔除")
            continue
        if 0.0 <= pct <= 1.0:
            out[k] = pct
        else:
            log(f"分位越界 {k}={pct}, 剔除")
    return out


def valuation_score(name, metrics):
    """metrics: {'pe': pct01, 'pb': pct01, 'dy': pct01} 分位越高越贵;dy 传原分位,内部反转"""
    weights = WEIGHTS.get(name, [("pe", 1.0)])
    used = []
    for key, w in weights:
        pct = metrics.get(key)
        if pct is None:
            continue
        if key == "dy":
            pct = 1.0 - pct  # 股息率分位高=便宜,反转
        used.append((w, max(0.0, min(1.0, pct))))
    if not used:
        return None, []
    tw = sum(w for w, _ in used)
    score = sum(w * p for w, p in used) / tw
    return score, [(w / tw, p) for w, p in used]


def dca_ratio(score):
    if score is None:
        return None
    for th, ratio in DCA_TIERS:
        if score >= th:
            return ratio
    return 1.0


def take_profit_signal(score, trend):
    """返回 (触发?, 原因)"""
    if score is None:
        return False, "数据不足"
    if score >= TP_SCORE:
        return True, f"估值评分{score*100:.0f}%≥95%"
    if score >= TP_SCORE_TREND and trend:
        if trend["below_days"] >= TREND_BREAK_DAYS and trend["slope_pct"] < 0:
            return True, f"评分{score*100:.0f}%≥90%+连续{trend['below_days']}日破SMA200+均线斜率{trend['slope_pct']:.1f}%"
        return False, f"评分{score*100:.0f}%≥90%但趋势未确认转弱"
    return False, ""


# ========== 邮件 ==========
def send_email(text_body, html_body, subject):
    user = os.environ.get("SMTP_USER", "").strip()
    code = os.environ.get("SMTP_CODE", "").strip()
    to = os.environ.get("SMTP_TO", "").strip() or user
    if not user or not code:
        return False
    try:
        from email.mime.multipart import MIMEMultipart
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = user
        msg["To"] = to
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as s:
            s.login(user, code)
            s.sendmail(user, [to], msg.as_string())
        return True
    except Exception as e:
        log(f"邮件发送失败: {e}")
        return False


def _score_color(score):
    """评分 -> (颜色, 标签)"""
    if score is None:
        return "#888888", "--"
    if score >= 0.85:
        return "#c0392b", "极端高估"
    if score >= 0.75:
        return "#e67e22", "高估"
    if score >= 0.60:
        return "#d4a017", "偏贵"
    if score >= 0.40:
        return "#27ae60", "正常偏高"
    return "#1e8449", "正常"


def _trend_color(status):
    return {"多头": "#1e8449", "转弱": "#e67e22", "空头": "#c0392b"}.get(status, "#888888")


def build_email_html(rows, any_alert, now):
    """HTML 版报告(手机邮件可视化)"""
    cards = []
    for r in rows:
        if "error" in r:
            cards.append(
                f'<table style="border-collapse:collapse;width:100%;margin:10px 0;border:1px solid #e74c3c;border-radius:8px;">'
                f'<tr><td style="padding:10px;background:#fdf3f2;font-size:14px;color:#c0392b;">⚠️ {r["name"]}: 获取失败 {r["error"][:60]}</td></tr></table>'
            )
            continue
        name = r["name"]
        score = r.get("score")
        color, vd = _score_color(score)
        trend = r.get("trend")
        t_status = trend["status"] if trend else "N/A"
        t_color = _trend_color(t_status)
        pe_s = f"{fmt(r.get('pe'))} <span style='color:#888'>({fmt(r.get('pe_pct')*100,1)}%)</span>" if r.get("pe") is not None else "--"
        pb_s = f"{fmt(r.get('pb'))} <span style='color:#888'>({fmt(r.get('pb_pct')*100,1)}%)</span>" if r.get("pb") is not None else "--"
        dy_s = f"{fmt(r.get('dy'),2)}%" if r.get("dy") is not None else "--"
        tp = r.get("tp")
        if tp:
            tp_html = "<span style='color:#c0392b;font-weight:bold;'>🚨 止盈触发</span>"
            tp_detail = f"<div style='font-size:12px;color:#c0392b;'>→ {r.get('tp_reason','')} → 分批卖出(1/3,间隔10%涨幅)</div>"
        else:
            tp_html = "<span style='color:#27ae60;'>未触发</span>"
            tp_detail = ""
        dca = r.get("dca")
        dca_s = f"{dca*100:.0f}%" if dca is not None else "--"
        if r.get("eps") is not None:
            g = r.get("eps_growth")
            g_s = f", 同比<b>{g:+.1f}%</b>" if g is not None else ""
            eps_line = f"<tr><td style='padding:3px 8px;color:#666;'>盈利(EPS)</td><td style='padding:3px 8px;'>{fmt(r['eps'])} ({r.get('eps_date','')}){g_s}</td></tr>"
        else:
            eps_line = "<tr><td style='padding:3px 8px;color:#666;'>盈利增长</td><td style='padding:3px 8px;color:#999;'>N/A(无数据源)</td></tr>"
        if r.get("pe_ref") or r.get("pb_ref") or r.get("dy_ref"):
            refs = [x for x in [r.get("pe_ref"), r.get("pb_ref"), r.get("dy_ref")] if x]
            ref_line = f"<tr><td style='padding:3px 8px;color:#999;font-size:11px;' colspan='2'>{' · '.join(refs)}</td></tr>"
        else:
            ref_line = ""
        stale_line = "<tr><td colspan='2' style='padding:3px 8px;color:#c0392b;font-size:12px;'>⚠️ 数据可能过期</td></tr>" if r.get("stale") else ""
        trend_cell = f'<span style="color:{t_color};font-weight:bold;">{t_status}</span>'
        if trend:
            trend_cell += f' <span style="color:#888;font-size:11px;">SMA200:{trend["sma200"]:.0f} 斜率:{trend["slope_pct"]:+.2f}% 破位:{trend["below_days"]}日</span>'
        cards.append(
            f'<table style="border-collapse:collapse;width:100%;margin:10px 0;border:1px solid #ddd;border-radius:8px;font-family:Arial,微软雅黑,sans-serif;">'
            f'<tr><td style="padding:10px;background:#f5f5f5;font-weight:bold;font-size:15px;">📊 {name}</td></tr>'
            f'<tr><td style="padding:8px 10px;font-size:13px;">'
            f'<table style="width:100%;font-size:13px;border-collapse:collapse;">'
            f'<tr><td style="padding:3px 8px;color:#666;width:90px;">PE</td><td style="padding:3px 8px;">{pe_s}</td></tr>'
            f'<tr><td style="padding:3px 8px;color:#666;">PB</td><td style="padding:3px 8px;">{pb_s}</td></tr>'
            f'<tr><td style="padding:3px 8px;color:#666;">股息率</td><td style="padding:3px 8px;">{dy_s}</td></tr>'
            f'{eps_line}{ref_line}{stale_line}'
            f'<tr><td style="padding:3px 8px;color:#666;">趋势</td><td style="padding:3px 8px;">{trend_cell}</td></tr>'
            f'</table>'
            f'<div style="margin-top:8px;padding-top:8px;border-top:1px dashed #ddd;font-size:13px;">'
            f'综合估值:<span style="color:{color};font-weight:bold;"> {vd}({score*100:.0f}%)</span> &nbsp; 新资金:<b>{dca_s}</b> &nbsp; 止盈:{tp_html}'
            f'</div>{tp_detail}'
            f'</td></tr></table>'
        )
    if any_alert:
        banner = ('<div style="background:#c0392b;color:#fff;padding:10px 14px;border-radius:8px;font-size:15px;font-weight:bold;margin:10px 0;">'
                  '🚨 止盈信号:有指数触发,按批次执行(每次1/3,间隔10%涨幅),卖出后继续定投</div>')
    else:
        banner = ('<div style="background:#27ae60;color:#fff;padding:10px 14px;border-radius:8px;font-size:15px;font-weight:bold;margin:10px 0;">'
                  '✅ 无止盈信号:新资金按比例投入,存量持有</div>')
    return (
        '<div style="font-family:Arial,微软雅黑,sans-serif;max-width:600px;margin:0 auto;">'
        f'<h2 style="color:#333;margin-bottom:4px;">📊 指数监控 {now:%Y-%m-%d %H:%M}</h2>'
        f'<div style="color:#999;font-size:12px;margin-bottom:12px;">估值管新资金 · 趋势管止盈 · 仓位调节器而非买卖开关</div>'
        f'{banner}{"".join(cards)}'
        '<div style="color:#999;font-size:11px;margin-top:14px;border-top:1px solid #eee;padding-top:8px;">'
        '股息率分位已反转(高=便宜);缺失指标自动归一化;纳指PB/股息率无公开源不参与评分。数据源:Yahoo/Multpl/worldperatio/新浪/蛋卷。</div>'
        '</div>'
    )


def build_email_text(rows, any_alert, now):
    """纯文本版(HTML 不可用时的 fallback)"""
    t = [f"📊 指数监控 {now:%Y-%m-%d %H:%M} (北京)", ""]
    for r in rows:
        if "error" in r:
            t.append(f"⚠️ {r['name']}: 获取失败 {r['error'][:60]}")
            t.append("")
            continue
        t.append("━" * 30)
        t.append(f"📊 {r['name']}")
        pe_s = f"{fmt(r.get('pe'))} (分位{fmt(r.get('pe_pct')*100,1)}%)" if r.get("pe") is not None else "--"
        pb_s = f"{fmt(r.get('pb'))} (分位{fmt(r.get('pb_pct')*100,1)}%)" if r.get("pb") is not None else "--"
        dy_s = f"{fmt(r.get('dy'),2)}%" if r.get("dy") is not None else "--"
        t.append(f"PE:{pe_s}  PB:{pb_s}  股息率:{dy_s}")
        score = r.get("score")
        if score is not None:
            _, vd = _score_color(score)
            t.append(f"综合估值:{vd}({score*100:.0f}%)")
        dca = r.get("dca")
        dca_s = f"{dca*100:.0f}%" if dca is not None else "--"
        tp = r.get("tp")
        tp_s = f"触发({r.get('tp_reason','')})" if tp else "未触发"
        t.append(f"新资金:{dca_s} | 存量:持有 | 止盈:{tp_s}")
        t.append("")
    if any_alert:
        t.append("🚨 有指数触发止盈线:分批执行(每次1/3,间隔10%涨幅),卖出后继续定投")
    else:
        t.append("✅ 无止盈信号:新资金按比例投入,存量持有")
    return "\n".join(t)


# ========== 主流程 ==========
def main():
    now = datetime.now(CN_TZ)
    lines = [f"📊 指数监控 {now:%Y-%m-%d %H:%M} (北京)", ""]

    danjuan = fetch_danjuan()
    rows = []

    for name, cfg in INDICES.items():
        r = {"name": name}
        try:
            metrics = {}
            trend = None
            src_parts = []

            if cfg["type"] == "us":
                price, dt_cn, stale, trend = fetch_us_price_trend(cfg["yahoo"])
                r["price"] = price
                r["stale"] = stale
                src_parts.append(f"Yahoo {dt_cn:%m-%d %H:%M}")
                if cfg["pe_src"] == "worldperatio":
                    wp = fetch_worldperatio(cfg["wp_slug"])
                    metrics["pe"] = wp["pct10"]
                    r["pe"] = wp["pe"]
                    r["pe_pct"] = wp["pct10"]
                    r["pe_ref"] = (f"10Y μ={wp['mu10']:.1f} σ={wp['sigma10']:.1f}(正态近似)"
                                   + (f" · 20Y分位{wp['pct20']*100:.0f}%" if wp["pct20"] else ""))
                    src_parts.append(f"WP {wp['date']}")
                else:  # multpl
                    d_s, pe, pct, n = fetch_multpl_series("s-p-500-pe-ratio", "PE")
                    metrics["pe"] = pct
                    r["pe"], r["pe_pct"] = pe, pct
                    r["pe_ref"] = f"近10年{n}月样本"
                    src_parts.append(f"Multpl {d_s}")
                    # PB
                    try:
                        _, pb, pb_pct, n_pb = fetch_multpl_series("s-p-500-price-to-book", "PB")
                        metrics["pb"] = pb_pct
                        r["pb"], r["pb_pct"] = pb, pb_pct
                        r["pb_ref"] = f"样本{n_pb}点"
                    except Exception as e:
                        log(f"标普PB失败: {e}")
                    # 股息率
                    try:
                        dy_date, dy, dy_pct, n_dy = fetch_multpl_series("s-p-500-dividend-yield", "DY")
                        metrics["dy"] = dy_pct
                        r["dy"], r["dy_pct"] = dy, dy_pct
                        r["dy_ref"] = f"数据至{dy_date},样本{n_dy}月"
                    except Exception as e:
                        log(f"标普股息率失败: {e}")
                    # EPS 同比(盈利增长)
                    try:
                        eps_series = fetch_multpl_full("s-p-500-earnings", "EPS")
                        if len(eps_series) >= 12:
                            _, eps = eps_series[-1]
                            prev_dt, prev_eps = eps_series[-13]
                            r["eps"], r["eps_date"] = eps, eps_series[-1][0].strftime("%Y-%m")
                            r["eps_growth"] = (eps / prev_eps - 1) * 100 if prev_eps else None
                            r["eps_prev"] = f"{prev_eps:.1f}({prev_dt.strftime('%Y-%m')})"
                    except Exception as e:
                        log(f"标普EPS失败: {e}")
            else:  # cn
                price, chg, trend = fetch_sina_trend(cfg["sina"])
                r["price"], r["chg"] = price, chg
                src_parts.append("新浪日线")
                if danjuan and cfg["danjuan_name"] in danjuan:
                    dj = danjuan[cfg["danjuan_name"]]
                    metrics["pe"] = dj["pe_pct"]
                    metrics["pb"] = dj["pb_pct"]
                    r["pe"], r["pe_pct"] = dj["pe"], dj["pe_pct"]
                    r["pb"], r["pb_pct"] = dj["pb"], dj["pb_pct"]
                    r["dy"] = dj["dy"]
                    r["dy_ref"] = "当前值(无分位源)"
                    src_parts.append(f"蛋卷 {dj['date']}")

            # 评分(先做数据合理性校验)
            metrics = sanitize_metrics(metrics)
            score, used = valuation_score(name, metrics)
            r["score"] = score
            r["dca"] = dca_ratio(score)
            r["trend"] = trend
            tp, tp_reason = take_profit_signal(score, trend)
            r["tp"], r["tp_reason"] = tp, tp_reason
            r["src"] = " · ".join(src_parts)
        except Exception as e:
            r["error"] = str(e)
        rows.append(r)

    # ---- 渲染 ----
    any_tp = False
    for r in rows:
        if "error" in r:
            lines.append(f"⚠️ {r['name']}: 获取失败 {r['error'][:60]}")
            lines.append("")
            continue
        name = r["name"]
        sep = "━" * 34
        lines.append(sep)
        lines.append(f"📊 {name}")
        # 估值指标行
        pe_s = f"{fmt(r.get('pe'))} (分位{fmt(r.get('pe_pct')*100,1)}%)" if r.get("pe") is not None else "--"
        pb_s = f"{fmt(r.get('pb'))} (分位{fmt(r.get('pb_pct')*100,1)}%)" if r.get("pb") is not None else "--"
        dy_s = f"{fmt(r.get('dy'),2)}%" if r.get("dy") is not None else "--"
        lines.append(f"PE:{pe_s}  PB:{pb_s}  股息率:{dy_s}")
        if r.get("pe_ref"):
            lines.append(f"  参考: {r['pe_ref']}")
        refs = []
        if r.get("pb_ref"):
            refs.append(f"PB {r['pb_ref']}")
        if r.get("dy_ref"):
            refs.append(f"股息率 {r['dy_ref']}")
        if refs:
            lines.append("  " + " · ".join(refs))
        # 盈利增长
        if r.get("eps") is not None:
            g = r.get("eps_growth")
            g_s = f",同比{g:+.1f}%" if g is not None else ""
            lines.append(f"  盈利(EPS):{fmt(r['eps'])} ({r.get('eps_date','')}){g_s}  [前值 {r.get('eps_prev','')}]")
        else:
            lines.append("  盈利增长: N/A(数据源不可得)")
        # 趋势
        if r.get("trend"):
            t = r["trend"]
            lines.append(f"  趋势:{t['status']}  SMA200:{t['sma200']:.0f}  斜率:{t['slope_pct']:+.2f}%  破位:{t['below_days']}日")
        else:
            lines.append("  趋势: N/A")
        # 综合估值
        score = r.get("score")
        if score is not None:
            if score >= 0.85: vd = "极端高估"
            elif score >= 0.75: vd = "高估"
            elif score >= 0.60: vd = "偏贵"
            elif score >= 0.40: vd = "正常偏高"
            else: vd = "正常"
            lines.append(f"  综合估值:{vd}(评分{score*100:.0f}%)")
        # 信号
        dca = r.get("dca")
        dca_s = f"{dca*100:.0f}%" if dca is not None else "--"
        tp = r.get("tp")
        if tp:
            any_tp = True
            lines.append(f"  💰 新资金:{dca_s}  📦 存量:继续持有  🚨 止盈:触发({r.get('tp_reason','')})")
            lines.append(f"     → 分批卖出(每次1/3,间隔10%涨幅),卖出后继续定投")
        else:
            lines.append(f"  💰 新资金:{dca_s}  📦 存量:继续持有  🚨 止盈:未触发")
        if r.get("src"):
            lines.append(f"  数据源:{r['src']}")
        if r.get("stale"):
            lines.append("  ⚠️ 数据可能过期")
        lines.append("")

    lines.append(sep)
    if any_tp:
        lines.append("🚨 **止盈信号:有指数触发,按批次执行(1/3,间隔10%涨幅)**")
    else:
        lines.append("✅ 无止盈信号:新资金按比例投入,存量持有")
    lines.append("")
    lines.append("> 逻辑:估值→新资金比例(仓位调节器);估值极高或(极高+趋势破位)→分批止盈(买卖开关)。")
    lines.append("> 股息率分位已反转(分位高=便宜);缺失指标自动归一化权重;纳指PB/股息率无公开源故不参与评分。")

    report = "\n".join(lines)
    print(report)
    # 邮件推送:HTML + 纯文本双格式
    sent = send_email(
        build_email_text(rows, any_tp, now),
        build_email_html(rows, any_tp, now),
        f"指数监控 {now:%m-%d} {'🚨有止盈信号' if any_tp else '正常'}",
    )
    log(f"邮件推送: {'成功' if sent else '未配置/跳过'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ 监控脚本异常: {type(e).__name__}: {e}")
        sys.exit(1)
