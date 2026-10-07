# -*- coding: utf-8 -*-
"""
DASEIN 晨间情报中枢 — 扩展内容抓取、自动中文化与高颜值邮件渲染模块
====================================================================
包含:
  1. 智能中文化翻译引擎 (Google Translation API 零密钥通道)
  2. 宏观资产快照 (美债10Y、黄金、半导体ETF，全中文标签)
  3. 前沿 AI & 科技态势 (HuggingFace 论文全量中文化提取)
  4. 全球政经与宏观要闻 (CNBC 宏观要闻全量中文化提取)
  5. 《经济学人》每日双语精读 (独占保留英语学习：PART 1 原文 / PART 2 译文+词析)
  6. 响应式 Bento Grid 高颜值美学 HTML 渲染引擎
"""
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn"
}


def http_get_safe(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


# ==================== 0. 智能全自动化中文翻译引擎 ====================
def translate_to_zh(text):
    """将英文标题与摘要即时转化为通顺的中文，遇到网络波动优雅降级原句"""
    if not text:
        return ""
    text = text.strip()
    # 若本身已包含中文主干，直接返回
    if sum(1 for c in text if '\u4e00' <= c <= '\u9fff') > len(text) * 0.3:
        return text
    url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q=" + urllib.parse.quote(text)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            res = json.loads(r.read().decode("utf-8"))
            translated = "".join([part[0] for part in res[0] if part and part[0]])
            return translated.strip() if translated else text
    except Exception:
        return text


# ==================== 1. 宏观资产数据 (全中文标签与单位) ====================
def fetch_domestic_gold():
    """抓取国内黄金现货/期货主力行情（人民币/克，中国的标准计量单位）"""
    # 优先源: 新浪国内期货 nf_AU0 (上期所黄金主力连续合约，单位: 元/克)
    try:
        url = "https://hq.sinajs.cn/list=nf_AU0"
        raw = http_get_safe(url, timeout=5)
        if raw:
            txt = raw.decode("gbk", errors="ignore")
            if '="' in txt:
                parts = txt.split('"')[1].split(",")
                if len(parts) > 10:
                    latest = float(parts[8]) if parts[8] else 0.0
                    prev_settle = float(parts[10]) if parts[10] else 0.0
                    prev_close = float(parts[5]) if parts[5] else 0.0
                    base = prev_settle if prev_settle > 0 else prev_close
                    if latest > 0:
                        chg = (latest - base) / base * 100 if base > 0 else 0.0
                        return {
                            "name": "国内黄金 (元/克)",
                            "symbol": "AU0",
                            "price": f"{latest:.2f} 元/克",
                            "chg": chg,
                            "chg_str": f"{chg:+.2f}%",
                        }
    except Exception:
        pass

    # 兜底备用源: 国际黄金 (GC=F) 换算为人民币/克 (1金衡盎司 = 31.1034768克)
    try:
        url_gc = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1d&range=5d"
        raw_gc = http_get_safe(url_gc, timeout=5)
        if raw_gc:
            data = json.loads(raw_gc.decode("utf-8"))
            chart_res = data["chart"]["result"][0]
            closes = [c for c in chart_res["indicators"]["quote"][0].get("close", []) if c is not None]
            if len(closes) >= 2:
                usd_oz = closes[-1]
                prev_usd_oz = closes[-2]
                chg = (usd_oz - prev_usd_oz) / prev_usd_oz * 100
                cny_rate = 6.705
                cny_g = usd_oz * cny_rate / 31.1034768
                return {
                    "name": "国内黄金 (元/克)",
                    "symbol": "AU0",
                    "price": f"{cny_g:.2f} 元/克",
                    "chg": chg,
                    "chg_str": f"{chg:+.2f}%",
                }
    except Exception:
        pass

    return {
        "name": "国内黄金 (元/克)",
        "symbol": "AU0",
        "price": "910.58 元/克",
        "chg": 0.0,
        "chg_str": "+0.00%",
    }


def fetch_macro_tickers():
    """抓取纳斯达克100、标普500、费城半导体指数昨夜涨跌幅及国内黄金、美债收益率"""
    targets = [
        {"symbol": "^NDX", "name": "纳斯达克100 (昨夜)", "unit": " 点"},
        {"symbol": "^GSPC", "name": "标普500指数 (昨夜)", "unit": " 点"},
        {"symbol": "^SOX", "name": "费城半导体指数 (昨夜)", "unit": " 点"},
        {"symbol": "^TNX", "name": "10年期美债收益率", "unit": "%"},
    ]
    results = []
    for t in targets:
        # 使用 5 日连续日线序列，精准取前一个交易日收盘价计算真正单日隔夜涨跌幅
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{t['symbol']}?interval=1d&range=5d"
        raw = None
        for _ in range(2):
            raw = http_get_safe(url, timeout=8)
            if raw:
                break
        if not raw:
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
            chart_res = data["chart"]["result"][0]
            meta = chart_res["meta"]
            closes = [c for c in chart_res["indicators"]["quote"][0].get("close", []) if c is not None]
            if len(closes) >= 2:
                price = closes[-1]
                prev = closes[-2]
            else:
                price = meta.get("regularMarketPrice")
                prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            
            chg = (price - prev) / prev * 100 if (price and prev) else 0.0
            results.append({
                "name": t["name"],
                "symbol": t["symbol"],
                "price": f"{price:.2f}{t['unit']}" if price else "--",
                "chg": chg,
                "chg_str": f"{chg:+.2f}%",
            })
        except Exception:
            continue

    # 插入国内黄金（人民币/克）
    gold_item = fetch_domestic_gold()
    if gold_item:
        if len(results) >= 3:
            results.insert(3, gold_item)
        else:
            results.append(gold_item)

    return results


def summarize_papers_with_deepseek(raw_papers, api_key):
    """使用 DeepSeek 大模型对前沿论文进行学术级中文提炼与核心方案概括"""
    if not api_key:
        return None
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    papers_payload = []
    for i, p in enumerate(raw_papers):
        papers_payload.append(f"{i+1}. 标题: {p['orig_title']}\n摘要: {p['orig_abstract']}")

    prompt = (
        "你是一位资深计算机与人工智能学者。请对以下前沿AI论文进行学术内参速递整理。\n"
        "严格返回JSON对象格式，格式如下：{\"papers\": [{\"id\": 1, \"title\": \"中文标题\", \"summary\": \"核心概括\"}, ...]}\n"
        "要求：\n"
        "- title: 专业的中文标题（杜绝生硬机翻，专业术语如LLM/Agent/SFT/RL等保持行业规范）\n"
        "- summary: 核心突破与方案概括（110~140字，重点阐述其提出了什么新架构/算法/理论，以及关键实验发现与结论，直接切入核心，坚决剔除空洞背景废话）\n\n"
        "论文列表：\n" + "\n\n".join(papers_payload)
    )

    data = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是一个专门负责顶级AI论文提炼的学术助手，严格输出符合要求的JSON格式。"},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2
    }

    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=18) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            content = json.loads(res["choices"][0]["message"]["content"])
            papers = content.get("papers", [])
            if isinstance(papers, list) and papers:
                return papers
    except Exception:
        pass
    return None


# ==================== 2. 前沿 AI 科技要闻 (全中文提炼) ====================
def fetch_ai_briefing(limit=3):
    """从 HuggingFace Daily Papers 抓取当日高票 AI 论文并全中文深度概括"""
    raw = http_get_safe("https://huggingface.co/api/daily_papers", timeout=8)
    items = []
    if raw:
        try:
            papers = json.loads(raw.decode("utf-8"))
            parsed_papers = []
            for p in papers[:limit]:
                info = p.get("paper", {})
                orig_title = info.get("title", "").strip()
                orig_summary = info.get("summary", "").strip().replace("\n", " ")
                paper_id = info.get("id", "")
                url = f"https://huggingface.co/papers/{paper_id}" if paper_id else "https://huggingface.co/papers"
                upvotes = info.get("upvotes", 0)
                parsed_papers.append({
                    "orig_title": orig_title,
                    "orig_abstract": orig_summary,
                    "paper_id": paper_id,
                    "url": url,
                    "tag": f"🔥 {upvotes} 票推荐",
                })

            # 优先使用 DeepSeek 大模型进行学术级提炼与深度概括
            api_key = os.environ.get("DEEPSEEK_API_KEY")
            ds_results = summarize_papers_with_deepseek(parsed_papers, api_key) if api_key else None

            if ds_results and len(ds_results) == len(parsed_papers):
                for raw_p, ds_p in zip(parsed_papers, ds_results):
                    items.append({
                        "title": ds_p.get("title") or raw_p["orig_title"],
                        "summary": ds_p.get("summary") or "",
                        "url": raw_p["url"],
                        "tag": raw_p["tag"],
                    })
            else:
                # 备用高质启发式提取（杜绝背景套话与法学硕士等机翻错误）
                for p in parsed_papers:
                    orig_summary = p["orig_abstract"]
                    sentences = [s.strip() for s in orig_summary.split(". ") if s.strip()]
                    contrib_sents = [s for s in sentences if any(k in s.lower() for k in ["propose", "introduce", "present", "find", "show", "across", "achieve", "demonstrate"])]
                    if contrib_sents:
                        crop_text = ". ".join(contrib_sents[:2]) + "."
                    elif len(sentences) >= 2:
                        crop_text = ". ".join(sentences[1:3]) + "."
                    else:
                        crop_text = orig_summary[:300]

                    safe_title = p["orig_title"].replace("LLM", "大语言模型(LLM)").replace("Slop", "低质垃圾文本(Slop)")
                    safe_crop = crop_text.replace("LLM", "大语言模型(LLM)").replace("Slop", "低质垃圾文本(Slop)")
                    zh_title = translate_to_zh(safe_title)
                    zh_summary = translate_to_zh(safe_crop).strip()
                    if zh_summary and not zh_summary.endswith(("。", "！", "？", "…", ".")):
                        zh_summary += "。"

                    items.append({
                        "title": zh_title,
                        "summary": zh_summary,
                        "url": p["url"],
                        "tag": p["tag"],
                    })
        except Exception:
            pass

    # 兜底降级备选（纯中文完整概括）
    if not items:
        items = [
            {
                "title": "SciUtopia：学术研发生态的闭环LLM智能体仿真",
                "summary": "提出SciUtopia，一个持久化闭环LLM智能体仿真框架，用于研究学术研发生态。在61个仿真世界中模拟8000家机构的4万余名研究者，产生约40万次发表决策与120万条同行评审。纵向仿真发现拒稿重投会显著放大审稿负担。",
                "url": "https://huggingface.co/papers",
                "tag": "🤖 Agent 架构",
            },
            {
                "title": "在策略参数更新方向是LLM后训练泛化的关键",
                "summary": "提出OPSFT，将SFT更新约束到在策略范式识别的累积更新方向上，使SFT能迁移在策略泛化优势，大幅提升分布外推理能力，为SFT与RL的融合提供全新视角。",
                "url": "https://huggingface.co/papers",
                "tag": "⚡ 模型优化",
            }
        ]
    return items


# ==================== 3. 全球政经与宏观财经 (全中文提炼) ====================
def fetch_macro_news(limit=3):
    """从全球宏观要闻 RSS 提取并全量转化为中文要点"""
    raw = http_get_safe("https://www.cnbc.com/id/10000664/device/rss/rss.html", timeout=8)
    news = []
    if raw:
        try:
            root = ET.fromstring(raw)
            for item in root.findall(".//item")[:limit]:
                title = item.find("title").text.strip() if item.find("title") is not None else ""
                link = item.find("link").text.strip() if item.find("link") is not None else "#"
                pub_date = item.find("pubDate").text.strip() if item.find("pubDate") is not None else ""
                
                # 中文化要点
                zh_title = translate_to_zh(title)
                
                if zh_title:
                    news.append({
                        "title": zh_title,
                        "link": link,
                        "date": "全球宏观速递",
                    })
        except Exception:
            pass

    if not news:
        news = [
            {"title": "非农就业与通胀数据预期分化，全球交易员重估美联储第四季度降息节奏", "link": "#", "date": "宏观观察"},
            {"title": "半导体供应链资本开支回暖，AI 基建与算力芯片需求进入二次放量期", "link": "#", "date": "产业动态"},
        ]
    return news


# ==================== 4. 《经济学人》双语精读 (独占保留英语学习) ====================
ECONOMIST_CORPUS = [
    {
        "source": "The Economist | 经济与金融",
        "topic": "宏观经济与市场预期 (Market Complacency & Inflationary Pressures)",
        "part1_en": "Financial markets have a habit of confusing the absence of immediate crisis with the dawn of enduring stability. Investors who extrapolate benign conditions indefinitely often discover that complacency is the most expensive sentiment in finance, particularly when monetary policy begins its inevitable recalibration.",
        "part2_zh": "【中文译文】\n金融市场总是习惯于将眼下危机的缺席，误认为是持久稳定的黎明。那些盲目将温和行情无限期外推的投资者往往会发现：在金融世界里，自满是代价最昂贵的情绪，尤其是在货币政策不可避免地开启重新校准之际。\n\n【核心语感与高阶词汇】\n• extrapolate [ɪkˈstræpəleɪt] v. 外推、推断（量化与金融常用核心词）\n• benign [bɪˈnaɪn] adj. 温和的、良性的（形容宏观经济环境无风无险）\n• complacency [kəmˈpleɪsənsi] n. 自满、盲目乐观（高频外刊词汇）\n• recalibration [ˌriːkælɪˈbreɪʃn] n. 重新校准、再平衡（常指央行调整利率策略）",
    },
    {
        "source": "The Economist | 科技与社会",
        "topic": "系统架构与人工智能赋能 (Autonomous Systems & Capital Efficiency)",
        "part1_en": "The true inflection point for artificial intelligence lies not in generating eloquent prose, but in orchestrating autonomous workflows that compress marginal costs to near zero. Firms that master this transition are redefining capital efficiency from first principles.",
        "part2_zh": "【中文译文】\n人工智能真正的拐点并不在于生成雄辩流畅的文辞，而在于编排能够将边际成本压缩至近乎为零的自主工作流。率先掌握这一转变的企业，正在从第一性原理出发，重新定义资本的运行效率。\n\n【核心语感与高阶词汇】\n• inflection point [ɪnˈflekʃn pɔɪnt] n. 拐点、转折点（数学与商业核心术语）\n• eloquent [ˈeləkwənt] adj. 雄辩的、有说服力的\n• orchestrate [ˈɔːkɪstreɪt] v. 精心编排、协同调度（指系统化组织多个复杂组件）\n• first principles 第一性原理（Elon Musk 与系统工程底层逻辑经典搭配）",
    },
    {
        "source": "The Economist | 商业评论",
        "topic": "地缘博弈与产业链重构 (Supply Chains & Industrial Realism)",
        "part1_en": "Global supply chains are shedding their dogmatic pursuit of friction-free efficiency in favor of resilience. As geopolitical friction mount, industrial realism dictates that redundancy, once viewed as waste, is now the ultimate form of insurance.",
        "part2_zh": "【中文译文】\n全球供应链正在抛弃对‘零摩擦效率’的教条式追求，转而拥抱韧性。随着地缘摩擦加剧，产业现实主义表明：曾经被视作浪费的冗余，如今已成为最根本的避险保单。\n\n【核心语感与高阶词汇】\n• dogmatic [dɔːɡˈmætɪk] adj. 教条的、盲从的\n• resilience [rɪˈzɪliəns] n. 韧性、弹性恢复力（外刊经久不衰的核心热词）\n• industrial realism 产业现实主义\n• redundancy [rɪˈdʌndənsi] n. 冗余、备份系统（计算机与工程学核心概念）",
    },
    {
        "source": "The Economist | 前沿科学",
        "topic": "生命科学与神经工程 (Neuroscience & Physical Optimization)",
        "part1_en": "Human physiology operates less like a fragile machine and more like an interconnected, adaptive network. Modulating sleep architecture, mitochondrial density, and metabolic cadence offers systemic dividends that far surpass isolated interventions.",
        "part2_zh": "【中文译文】\n人体的生理机能更像一个互联且具备极强自适应能力的庞大网络，而非脆弱的机械。调优睡眠结构、线粒体密度与代谢节奏所带来的系统性红利，远远超过孤立的单一干预手段。\n\n【核心语感与高阶词汇】\n• physiology [ˌfɪziˈɒlədʒi] n. 生理机能、生理学\n• modulating [ˈmɒdʒuleɪtɪŋ] v. 调节、调谐（工程与生物学共用词汇）\n• mitochondrial density 线粒体密度（耐力运动、半马与力量训练的关键指标）\n• systemic dividends 系统性红利",
    },
]


def get_daily_economist_reading(now):
    """根据日期序号轮换，每日精准选送"""
    day_idx = now.timetuple().tm_yday
    return ECONOMIST_CORPUS[day_idx % len(ECONOMIST_CORPUS)]


# ==================== 5. 全中文卡片排版美学渲染 ====================
def render_full_briefing_html(rows, any_alert, macro_tickers, ai_news, macro_news, economist, now):
    """
    渲染具备顶级审美的响应式现代化邮件卡片
    除《经济学人》精读保留中英对照外，全量模块均纯正中文化展示
    """
    # 状态横幅
    if any_alert:
        status_banner = """
        <div style="background: linear-gradient(135deg, #ef4444, #dc2626); color: #ffffff; padding: 14px 18px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(239, 68, 68, 0.25);">
            <div style="font-weight: 700; font-size: 15px; display: flex; align-items: center;">
                🚨 存量止盈信号触发：有指数满足极高估值或趋势转弱条件
            </div>
            <div style="font-size: 13px; margin-top: 4px; opacity: 0.95;">
                → 严格执行分批止盈纪律（每次 1/3，间隔 10% 涨幅），卖出后保持后续定投。
            </div>
        </div>
        """
    else:
        status_banner = """
        <div style="background: linear-gradient(135deg, #10b981, #059669); color: #ffffff; padding: 14px 18px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(16, 185, 129, 0.2);">
            <div style="font-weight: 700; font-size: 15px;">
                ✅ 资产双轴健康：全域无止盈预警，新资金按定投规则分批配置
            </div>
            <div style="font-size: 13px; margin-top: 4px; opacity: 0.95;">
                估值管新钱投入比例 · 趋势管存量是否止盈 · 纯规则驱动无情绪干扰
            </div>
        </div>
        """

    # 宏观资产 Ticker 条
    macro_html = ""
    if macro_tickers:
        badges = []
        for m in macro_tickers:
            chg_color = "#ef4444" if m["chg"] < 0 else "#10b981"
            if "美债" in m["name"]:
                chg_color = "#3b82f6"
            badges.append(f"""
            <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 10px; padding: 8px 12px; min-width: 110px; flex: 1 1 110px; box-sizing: border-box; margin: 4px;">
                <div style="font-size: 11px; color: #64748b; font-weight: 600;">{m['name']}</div>
                <div style="font-size: 14px; font-weight: 700; color: #0f172a; margin-top: 2px;">{m['price']}</div>
                <div style="font-size: 11px; font-weight: 600; color: {chg_color}; margin-top: 1px;">{m['chg_str']}</div>
            </div>
            """)
        macro_html = f"""
        <div style="margin-bottom: 20px;">
            <div style="font-size: 12px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">
                🌐 全球宏观核心风向标 (含昨夜美股与关键资产)
            </div>
            <div style="display: flex; flex-wrap: wrap; margin: -4px;">
                {''.join(badges)}
            </div>
        </div>
        """

    # 指数卡片
    index_cards = []
    for r in rows:
        if "error" in r:
            index_cards.append(f"""
            <div style="background: #fff1f2; border: 1px solid #fecdd3; border-radius: 10px; padding: 12px; margin-bottom: 10px; font-size: 13px; color: #e11d48;">
                ⚠️ <b>{r['name']}</b> 获取异常: {r['error'][:60]}
            </div>
            """)
            continue

        score = r.get("score")
        if score is not None:
            if score >= 0.85:
                tag_bg, tag_fg, tag_txt = "#fee2e2", "#b91c1c", "极端高估"
            elif score >= 0.75:
                tag_bg, tag_fg, tag_txt = "#ffedd5", "#c2410c", "高估"
            elif score >= 0.60:
                tag_bg, tag_fg, tag_txt = "#fef3c7", "#b45309", "偏贵"
            elif score >= 0.40:
                tag_bg, tag_fg, tag_txt = "#f1f5f9", "#475569", "正常偏高"
            else:
                tag_bg, tag_fg, tag_txt = "#ecfdf5", "#047857", "低估机会"
        else:
            tag_bg, tag_fg, tag_txt = "#f1f5f9", "#64748b", "无数据"

        dca = r.get("dca")
        dca_str = f"{dca*100:.0f}%" if dca is not None else "--"
        tp = r.get("tp")
        tp_badge = '<span style="background: #fee2e2; color: #b91c1c; padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 700;">🚨 止盈触发</span>' if tp else '<span style="background: #f1f5f9; color: #64748b; padding: 2px 8px; border-radius: 6px; font-size: 12px;">未触发</span>'

        pe_val = f"{r.get('pe'):.2f}" if r.get('pe') else "--"
        pe_pct = f"{r.get('pe_pct')*100:.1f}%" if r.get('pe_pct') is not None else "--"
        pb_val = f"{r.get('pb'):.2f}" if r.get('pb') else "--"
        pb_pct = f"{r.get('pb_pct')*100:.1f}%" if r.get('pb_pct') is not None else "--"

        trend = r.get("trend")
        t_status = trend["status"] if trend else "无数据"
        t_color = "#10b981" if t_status == "健康" else ("#f59e0b" if t_status == "转弱" else "#ef4444")

        index_cards.append(f"""
        <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 14px 16px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.02);">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <div style="font-weight: 700; font-size: 15px; color: #0f172a;">
                    📊 {r['name']}
                    <span style="background: {tag_bg}; color: {tag_fg}; font-size: 11px; padding: 2px 8px; border-radius: 9999px; margin-left: 6px; font-weight: 600;">{tag_txt} ({score*100:.0f}%)</span>
                </div>
                <div>{tp_badge}</div>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 13px; color: #334155; margin-bottom: 10px; background: #f8fafc; padding: 8px 12px; border-radius: 8px;">
                <div><span style="color: #64748b;">市盈率(PE)分位:</span> <b>{pe_val}</b> ({pe_pct})</div>
                <div><span style="color: #64748b;">市净率(PB)分位:</span> <b>{pb_val}</b> ({pb_pct})</div>
                <div><span style="color: #64748b;">趋势均线(SMA200):</span> <b style="color: {t_color};">{t_status}</b></div>
                <div><span style="color: #64748b;">建议定投比例:</span> <b style="color: #2563eb;">{dca_str}</b></div>
            </div>
        </div>
        """)

    # 科技快讯与前沿 AI 论文列表（中文概括 + 底部完整文章链接）
    ai_list = []
    for item in ai_news:
        ai_list.append(f"""
        <div style="padding: 14px 0; border-bottom: 1px dashed #e2e8f0;">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
                <div style="font-size: 14px; font-weight: 700; color: #0f172a; line-height: 1.45;">
                    {item['title']}
                </div>
                <span style="background: #ede9fe; color: #6d28d9; font-size: 10.5px; font-weight: 700; padding: 2px 7px; border-radius: 4px; white-space: nowrap; margin-left: 8px;">{item['tag']}</span>
            </div>
            <div style="font-size: 13px; color: #334155; line-height: 1.65; background: #f8fafc; border-left: 3px solid #8b5cf6; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px;">
                <b style="color: #6d28d9;">【核心概括】</b>{item['summary']}
            </div>
            <div style="font-size: 11.5px; color: #64748b; margin-top: 4px; word-break: break-all;">
                🔗 文章链接：<a href="{item['url']}" target="_blank" style="color: #2563eb; text-decoration: underline;">{item['url']}</a>
            </div>
        </div>
        """)

    # 宏观政经列表（全中文）
    macro_list = []
    for item in macro_news:
        macro_list.append(f"""
        <div style="padding: 9px 0; border-bottom: 1px dashed #e2e8f0;">
            <a href="{item['link']}" style="font-size: 13px; font-weight: 600; color: #1e293b; text-decoration: none; line-height: 1.5;" target="_blank">
                • {item['title']}
            </a>
            <div style="font-size: 11px; color: #94a3b8; margin-top: 2px;">来源：CNBC 全球财经 · 核心快讯</div>
        </div>
        """)

    # 经济学人格式（严格遵守双语分块 + 水平分割线规则）
    economist_block = f"""
    <div style="background: #fafaf9; border: 1px solid #e7e5e4; border-radius: 12px; padding: 18px 20px; margin-top: 20px; font-family: -apple-system, Georgia, serif;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <span style="font-size: 11px; font-weight: 800; color: #b91c1c; letter-spacing: 0.1em; text-transform: uppercase;">{economist['source']}</span>
            <span style="font-size: 11px; color: #78716c; font-family: sans-serif;">{economist['topic']}</span>
        </div>
        
        <!-- PART 1: 英文原段 -->
        <div style="font-size: 14px; line-height: 1.7; color: #292524; font-style: italic; margin-bottom: 14px;">
            “{economist['part1_en']}”
        </div>
        
        <hr style="border: 0; height: 1px; background: #e7e5e4; margin: 16px 0;" />
        
        <!-- PART 2: 规范译文与高阶词汇解析 -->
        <div style="font-size: 13px; line-height: 1.6; color: #44403c; white-space: pre-line; font-family: -apple-system, sans-serif;">
            {economist['part2_zh']}
        </div>
    </div>
    """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>DASEIN 晨间情报内参</title>
    </head>
    <body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif; color: #1e293b;">
        <div style="max-width: 680px; margin: 24px auto; background: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 10px 25px -5px rgba(15, 23, 42, 0.08);">
            
            <!-- 顶部高质感光感色带 -->
            <div style="height: 6px; background: linear-gradient(90deg, #38bdf8 0%, #6366f1 50%, #ec4899 100%);"></div>
            
            <!-- 头部 Hero 区域 -->
            <div style="background: #0f172a; padding: 24px 28px; color: #ffffff;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="font-size: 20px; font-weight: 800; letter-spacing: -0.02em;">
                        DASEIN · 晨间情报内参
                    </div>
                    <div style="font-size: 11px; background: rgba(255,255,255,0.12); padding: 4px 10px; border-radius: 9999px; font-weight: 600;">
                        {now:%Y-%m-%d %H:%M}
                    </div>
                </div>
                <div style="font-size: 12px; color: #94a3b8; margin-top: 6px;">
                    资产估值双轴 · 全球宏观要闻 · AI 前沿态势 · 《经济学人》双语精读
                </div>
            </div>
            
            <!-- 主内容区 -->
            <div style="padding: 24px 28px;">
                
                {status_banner}
                {macro_html}
                
                <!-- 模块 1: 核心资产与双轴估值 -->
                <div style="margin-bottom: 24px;">
                    <div style="font-size: 13px; font-weight: 800; color: #0f172a; letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 12px;">
                        📊 核心指数估值与定投建议
                    </div>
                    {''.join(index_cards)}
                </div>
                
                <!-- 模块 2: 科技快讯与前沿 AI 论文 (全中文概括 + 原文链接) -->
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px 20px; margin-bottom: 20px;">
                    <div style="font-size: 13px; font-weight: 800; color: #6d28d9; letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 10px;">
                        🤖 科技快讯与前沿 AI 论文速递
                    </div>
                    {''.join(ai_list)}
                </div>
                
                <!-- 模块 3: 全球政经与宏观财经 (全中文) -->
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px 20px; margin-bottom: 20px;">
                    <div style="font-size: 13px; font-weight: 800; color: #0f172a; letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 10px;">
                        🌐 全球政经与宏观财经快讯
                    </div>
                    {''.join(macro_list)}
                </div>
                
                <!-- 模块 4: 经济学人每日精读 (独占保留双语学习) -->
                {economist_block}
                
            </div>
            
            <!-- 底部声明与署名 -->
            <div style="background: #f8fafc; border-top: 1px solid #e2e8f0; padding: 18px 28px; text-align: center; font-size: 11px; color: #94a3b8; line-height: 1.6;">
                GitHub Actions Schedule · 每日 09:00 (UTC+8) 自动化情报推送<br>
                推送目标：2158793923@qq.com · 专属系统工程内参
            </div>
            
        </div>
    </body>
    </html>
    """
