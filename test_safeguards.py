#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF 轮动监控系统 - 全维度兜底措施单元测试与边界用例验证套件
覆盖:
  1. 数据源主备兜底 (Dual Data Provider)
  2. 单标的故障隔离 (Symbol Isolation)
  3. 大盘基准失效兜底 (Regime Fallback)
  4. 推送与展示双重兜底 (Step Summary Fallback)
  5. 数值安全兜底 (Zero-Division & Edge Guards)
"""

import os
import sys
import math
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

# 导入待测模块
sys.path.insert(0, r"D:\ai生成\index-take-profit-monitor")
import etf_rotation_monitor as monitor


class TestDualDataProvider(unittest.TestCase):
    """测试主备数据源与优雅降级"""

    def test_shanghai_index_live_fetch(self):
        """测试实盘获取上证指数，保证主源或备用源能成功返回"""
        res = monitor.fetch_shanghai_index()
        self.assertIsNotNone(res, "上证指数获取结果不应为 None")
        self.assertIn("price", res)
        self.assertGreater(res["price"], 1000.0)
        self.assertIn(res["provider"], ["腾讯财经", "新浪财经"])

    def test_shanghai_index_fallback_to_sina(self):
        """模拟腾讯接口异常时，自动无缝降级到新浪备用接口"""
        with patch("etf_rotation_monitor.http_get_direct") as mock_get:
            def side_effect(url, headers=None, timeout=6):
                if "gtimg.cn" in url:
                    raise urllib.error.URLError("Simulated Tencent timeout")
                return b'var hq_str_sh000001="\xc9\xcf\xd6\xa4\xd6\xb8\xca\xfd,3600.0,3610.0,3605.5,3620.0,3590.0,0,0,100,200,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2026-10-30,15:00:00,00,";'
            mock_get.side_effect = side_effect
            res = monitor.fetch_shanghai_index()
            self.assertIsNotNone(res)
            self.assertEqual(res["provider"], "新浪财经")
            self.assertEqual(res["price"], 3605.5)

    def test_kline_fallback_to_sina(self):
        """模拟腾讯 K 线接口失败时，自动切换至新浪 K 线接口"""
        with patch("etf_rotation_monitor.http_get_direct") as mock_get:
            def side_effect(url, headers=None, timeout=8):
                if "gtimg.cn" in url:
                    raise Exception("Tencent 502 Bad Gateway")
                return b'[{"day":"2026-10-29","open":"1.20","high":"1.25","low":"1.19","close":"1.22","volume":"100000"},{"day":"2026-10-30","open":"1.22","high":"1.26","low":"1.21","close":"1.24","volume":"150000"}]'
            mock_get.side_effect = side_effect
            records, provider = monitor.fetch_kline_data("sh512890", count=2)
            self.assertEqual(provider, "新浪财经")
            self.assertEqual(len(records), 2)
            self.assertEqual(records[-1]["close"], 1.24)


class TestSymbolIsolation(unittest.TestCase):
    """测试单标的故障隔离"""

    def test_faulty_symbol_isolated_without_crashing_portfolio(self):
        """当标的池中某标的停牌或网络失败时，其余标的正常计算，故障标的被隔离"""
        mock_universe = {
            "512890": {"name": "红利低波", "symbol": "sh512890", "group": "防御金融"},
            "512800": {"name": "银行",     "symbol": "sh512800", "group": "防御金融"},
            "159558": {"name": "半导设备", "symbol": "sz159558", "group": "科技成长"},
        }
        with patch.dict(monitor.ETF_UNIVERSE, mock_universe, clear=True):
            def mock_fetch(info):
                if info["symbol"] == "sz159558":
                    # 模拟半导设备停牌/数据获取失败
                    return [], False, "标的停牌或数据源异常"
                # 正常标的生成 30 天连续 K 线
                dates = [f"2026-10-{i:02d}" for i in range(1, 31)]
                base = 1.0 if info["symbol"] == "sh512890" else 0.8
                recs = [
                    {"date": d, "open": base, "high": base + 0.02, "low": base - 0.01, "close": base + i * 0.005, "vol": 500000.0}
                    for i, d in enumerate(dates)
                ]
                return recs, True, "数据正常"

            with patch("etf_rotation_monitor.fetch_etf_history_and_live", side_effect=mock_fetch):
                with patch("etf_rotation_monitor.fetch_shanghai_index") as mock_sh:
                    mock_sh.return_value = {"price": 3600.0, "change_pct": 0.5, "provider": "腾讯"}
                    analysis = monitor.compute_portfolio_signals()

                    self.assertIsNotNone(analysis)
                    self.assertEqual(len(analysis["isolated_etfs"]), 1)
                    self.assertEqual(analysis["isolated_etfs"][0]["code"], "159558")
                    self.assertIn("159558", [x["code"] for x in analysis["ranked_etfs"] if x.get("is_isolated")])
                    # 其余标的正常完成轮动推荐
                    self.assertIn(analysis["target_etf_1"], ["512890", "512800"])


class TestRegimeFallback(unittest.TestCase):
    """测试大盘基准失效兜底 (Regime Fallback)"""

    def test_shanghai_index_missing_triggers_conservative_defense(self):
        """当上证指数无法获取时，强制进入保守防御模式 (50% 红利低波 + 50% 现金)"""
        with patch("etf_rotation_monitor.fetch_shanghai_index", return_value=None):
            analysis = monitor.compute_portfolio_signals()
            self.assertTrue(analysis["is_bear_regime"])
            self.assertEqual(analysis["target_etf_1"], "512890")
            self.assertEqual(analysis["target_etf_2"], "CASH")
            self.assertIn("大盘基准失效兜底", analysis["target_reason"])


class TestStepSummaryFallback(unittest.TestCase):
    """测试 Step Summary 自动写入"""

    def test_write_github_step_summary(self):
        """测试在配置 GITHUB_STEP_SUMMARY 环境变量时能够正确追加 Markdown 内容"""
        with tempfile.NamedTemporaryFile(mode="w+", delete=False, encoding="utf-8") as tmp:
            tmp_path = tmp.name

        try:
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": tmp_path}):
                sample_md = "### 测试内参标题\n> 这是一个 Step Summary 测试"
                ret = monitor.write_github_step_summary(sample_md)
                self.assertTrue(ret)
                with open(tmp_path, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn("测试内参标题", content)
                self.assertIn("这是一个 Step Summary 测试", content)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class TestZeroDivisionAndEdgeGuards(unittest.TestCase):
    """测试数值安全兜底与边界极端值"""

    def test_zero_variance_in_factor_calc(self):
        """测试 20 天所有收盘价完全一样（方差/标准差=0）时的数值保护"""
        dates = [f"2026-10-{i:02d}" for i in range(1, 30)]
        recs = [
            {"date": d, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "vol": 1000.0}
            for d in dates
        ]
        fac = monitor.calculate_factors_for_etf(recs)
        self.assertIsNotNone(fac)
        self.assertEqual(fac["current_close"], 1.0)
        self.assertEqual(fac["daily_change_pct"], 0.0)
        self.assertFalse(math.isnan(fac["composite_score"] if "composite_score" in fac else 0.0))
        self.assertFalse(math.isinf(fac["boll_bias_pct"]))

    def test_insufficient_records_guard(self):
        """测试历史数据少于 25 天时安全返回 None，不抛异常崩溃"""
        recs = [
            {"date": f"2026-10-{i:02d}", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "vol": 100.0}
            for i in range(1, 15)
        ]
        fac = monitor.calculate_factors_for_etf(recs)
        self.assertIsNone(fac)

    def test_optimal_lots_edge_cases(self):
        """测试资金为 0、负数或极小时的整数规划求解安全保护"""
        # 资金为 0
        r0 = monitor.find_optimal_lots(100.0, 100.0, 0.0)
        self.assertEqual(r0["x1"], 0)
        self.assertEqual(r0["x2"], 0)
        # 单价为 0
        r1 = monitor.find_optimal_lots(0.0, 100.0, 1000.0)
        self.assertEqual(r1["x1"], 0)
        # 资金不足 1 手
        r2 = monitor.find_optimal_lots(500.0, 600.0, 200.0)
        self.assertEqual(r2["spent"], 0.0)
        self.assertEqual(r2["cash_left"], 200.0)

    def test_zero_stdev_zscore_guard(self):
        """测试所有标的动量得分完全相同时，截面标准化不发生除零崩溃"""
        mock_universe = {
            "512890": {"name": "红利低波", "symbol": "sh512890", "group": "防御金融"},
            "512800": {"name": "银行",     "symbol": "sh512800", "group": "防御金融"},
        }
        with patch.dict(monitor.ETF_UNIVERSE, mock_universe, clear=True):
            dates = [f"2026-10-{i:02d}" for i in range(1, 31)]
            # 两只 ETF 价格走势完全相同，导致动量方差为 0
            recs = [
                {"date": d, "open": 1.0 + i * 0.01, "high": 1.02 + i * 0.01, "low": 0.98 + i * 0.01, "close": 1.0 + i * 0.01, "vol": 10000.0}
                for i, d in enumerate(dates)
            ]
            with patch("etf_rotation_monitor.fetch_etf_history_and_live", return_value=(recs, True, "数据正常")):
                with patch("etf_rotation_monitor.fetch_shanghai_index", return_value={"price": 3600.0, "change_pct": 0.5, "provider": "腾讯"}):
                    analysis = monitor.compute_portfolio_signals()
                    self.assertIsNotNone(analysis)
                    for r in analysis["ranked_etfs"]:
                        self.assertFalse(math.isnan(r["composite_score"]))
                        self.assertFalse(math.isinf(r["composite_score"]))

    def test_allocation_with_all_tiers(self):
        """测试 1K / 5K / 1M 资金档位分配无异常且资金利用率合理"""
        res_1k = monitor.optimize_portfolio_allocation("512890", 1.25, "512800", 0.75, 1000.0)
        self.assertGreater(res_1k["utilization_pct"], 80.0)
        self.assertLessEqual(res_1k["total_spent"], 1000.0)

        res_5k = monitor.optimize_portfolio_allocation("512890", 1.25, "512800", 0.75, 5000.0)
        self.assertGreater(res_5k["utilization_pct"], 95.0)
        self.assertLessEqual(res_5k["total_spent"], 5000.0)

        res_1m = monitor.optimize_portfolio_allocation("512890", 1.25, "512800", 0.75, 1000000.0)
        self.assertGreater(res_1m["utilization_pct"], 99.9)
        self.assertLessEqual(res_1m["total_spent"], 1000000.0)


if __name__ == "__main__":
    unittest.main()
