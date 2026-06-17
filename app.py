#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股-A股映射雷达站 v4 — 25板块+券商研报风格+指数映射
"""
import json, os, time, re, threading, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from flask import Flask, jsonify, render_template_string

APP_DIR = Path(__file__).parent
REFRESH_INTERVAL = 300
API_TIMEOUT = 12

with open(APP_DIR / "mapping.json", "r", encoding="utf-8") as f:
    MAPPING = json.load(f)

# 指数映射配置：美股指数 ↔ A股指数
INDEX_CONFIG = [
    {"usTicker": "^SOX", "usName": "SOX", "usLabel": "费城半导体", "aCode": "sh512480", "aName": "半导体ETF", "aLabel": "半导体"},
    {"usTicker": "^NDX", "usName": "NDX", "usLabel": "纳指100", "aCode": "sz399006", "aName": "创业板指", "aLabel": "创业板"},
    {"usTicker": "^GSPC", "usName": "SPX", "usLabel": "标普500", "aCode": "sh000300", "aName": "沪深300", "aLabel": "沪深300"},
    {"usTicker": "^DJI", "usName": "DJI", "usLabel": "道琼斯", "aCode": "sh000001", "aName": "上证指数", "aLabel": "上证"},
]

class Fetcher:
    def __init__(self):
        self.cache = {"us": {}, "a": {}, "sectors": [], "indices": {}, "updatedAt": None, "marketStatus": {}}
        self.lock = threading.Lock()
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def _fetch_us(self, tickers):
        results = {}
        for t in tickers:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?interval=1d&range=5d"
                r = requests.get(url, headers=self.headers, timeout=10)
                data = r.json()["chart"]["result"][0]
                c = data["indicators"]["quote"][0].get("close", [])
                if len(c) >= 2 and c[-1] and c[-2]:
                    cur, prev = c[-1], c[-2]
                    pct = (cur - prev) / prev * 100
                    results[t] = {"price": round(cur, 2), "prevClose": round(prev, 2), "change": round(cur - prev, 2), "changePercent": round(pct, 2), "status": "ok"}
                else:
                    results[t] = {"error": "no data"}
            except Exception as e:
                results[t] = {"error": str(e)}
            time.sleep(0.1)
        return results

    def _fetch_a(self, stocks):
        qlist = ",".join(f"{s['market'].lower()}{s['code']}" for s in stocks)
        url = f"http://qt.gtimg.cn/q={qlist}"
        r = requests.get(url, headers=self.headers, timeout=15)
        text = r.content.decode("gbk", errors="ignore")
        results = {}
        for line in text.split(";"):
            line = line.strip()
            if not line or not line.startswith("v_"):
                continue
            m = re.search(r'v_(\w+)="(.+)"', line)
            if not m:
                continue
            fields = m.group(2).split("~")
            if len(fields) < 5:
                continue
            code, name, price, prev = fields[2], fields[1], float(fields[3]), float(fields[4])
            change = price - prev
            pct = (change / prev) * 100 if prev else 0
            results[code] = {"code": code, "name": name, "price": round(price, 2), "prevClose": round(prev, 2), "change": round(change, 2), "changePercent": round(pct, 2), "status": "ok"}
        return results

    def _fetch_indices(self):
        """获取指数映射数据：美股指数 + A股指数"""
        indices = {}
        # 美股指数（Yahoo Finance）
        us_tickers = [idx["usTicker"] for idx in INDEX_CONFIG]
        us_data = self._fetch_us(us_tickers)
        for idx in INDEX_CONFIG:
            t = idx["usTicker"]
            info = us_data.get(t, {})
            if info.get("status") == "ok":
                indices[t] = {
                    "price": info["price"],
                    "change": info["change"],
                    "changePercent": info["changePercent"],
                    "status": "ok"
                }
            else:
                indices[t] = {"error": info.get("error", "fetch failed"), "status": "error"}
        # A股指数（腾讯行情）
        a_codes = [{"market": idx["aCode"][:2].upper(), "code": idx["aCode"][2:]} for idx in INDEX_CONFIG]
        a_data = self._fetch_a(a_codes)
        for idx in INDEX_CONFIG:
            ac = idx["aCode"][2:]
            info = a_data.get(ac, {})
            if info.get("status") == "ok":
                indices[idx["aCode"]] = {
                    "price": info["price"],
                    "change": info["change"],
                    "changePercent": info["changePercent"],
                    "status": "ok"
                }
            else:
                indices[idx["aCode"]] = {"error": info.get("error", "fetch failed"), "status": "error"}
        return indices

    def refresh(self):
        print(f"[{datetime.now()}] 开始刷新...")
        all_us = set()
        all_a = []
        for s in MAPPING["sectors"]:
            for u in s["usLeaders"]:
                all_us.add(u["ticker"])
            for a in s["aTargets"]:
                all_a.append(a)
        seen = set()
        uniq_a = [s for s in all_a if not (s["code"] in seen or seen.add(s["code"]))]
        us_data = self._fetch_us(list(all_us))
        a_data = self._fetch_a(uniq_a)
        # 获取指数映射数据
        indices_data = self._fetch_indices()
        sectors = []
        for sector in MAPPING["sectors"]:
            # 跳过纯指数映射板块（已在 indices 中展示）
            if sector.get("type") == "index":
                continue
            tw = sum(u.get("weight", 1) for u in sector["usLeaders"])
            wp = 0.0
            usl = []
            for u in sector["usLeaders"]:
                info = us_data.get(u["ticker"], {})
                p = info.get("changePercent")
                if p is not None:
                    wp += p * u.get("weight", 1)
                usl.append({"ticker": u["ticker"], "name": u["name"], "weight": u.get("weight", 1), **info})
            wp = wp / tw if tw else 0
            at = []
            for a in sector["aTargets"]:
                info = a_data.get(a["code"], {})
                at.append({"code": a["code"], "name": a["name"], "market": a["market"], "weight": a.get("weight", 1), **info})
            g, gt, gc = "neutral", "中性", "gray"
            if wp is None:
                g, gt, gc = "unknown", "暂无", "gray"
            elif wp >= 5:
                g, gt, gc = "strong_positive", "强利好", "red"
            elif wp >= 2:
                g, gt, gc = "positive", "利好", "red"
            elif wp > -2:
                g, gt, gc = "neutral", "中性", "gray"
            elif wp > -5:
                g, gt, gc = "negative", "利空", "green"
            else:
                g, gt, gc = "strong_negative", "强利空", "green"
            sectors.append({"id": sector["id"], "name": sector["name"], "nameEn": sector["nameEn"], "usWeightedPct": round(wp, 2), "usLeaders": usl, "aTargets": at, "guidance": {"level": g, "text": gt, "color": gc}})
        sectors.sort(key=lambda x: abs(x.get("usWeightedPct", 0)), reverse=True)
        now = datetime.now(timezone(timedelta(hours=8)))
        t = now.time()
        wd = now.weekday()
        us_open = t.hour >= 21 or t.hour <= 5
        a_open = wd < 5 and ((9 <= t.hour <= 10) or (t.hour == 11 and t.minute <= 30) or (13 <= t.hour <= 14) or (t.hour == 15 and t.minute == 0))
        a_label = "A股交易中" if a_open else ("A股未开盘" if wd < 5 and t.hour < 9 else "A股已收盘")
        ms = {"time": now.strftime("%Y-%m-%d %H:%M"), "us": "美股交易中" if us_open else "美股已收盘", "a": a_label, "isUSOpen": us_open, "isAOpen": a_open}
        with self.lock:
            self.cache = {"us": us_data, "a": a_data, "sectors": sectors, "indices": indices_data, "updatedAt": now.isoformat(), "marketStatus": ms}
        print(f"[{datetime.now()}] 刷新完成 US={len(us_data)} A={len(a_data)} Indices={len(indices_data)}")

fetcher = Fetcher()
app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(HTML_PAGE)

@app.route("/api/data")
def api_data():
    with fetcher.lock:
        return jsonify(fetcher.cache)

def _bg():
    while True:
        try:
            fetcher.refresh()
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(REFRESH_INTERVAL)

HTML_PAGE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="format-detection" content="telephone=no">
<title>美股-A股映射雷达</title>
<script src="https://html2canvas.hertzen.com/dist/html2canvas.min.js"></script>
<style>
/* ===== 基础重置 ===== */
* { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html { font-size: 14px; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", sans-serif;
  background: #ffffff;
  color: #333333;
  line-height: 1.35;
  -webkit-font-smoothing: antialiased;
}

/* ===== 颜色系统：红/绿/灰 ===== */
.up { color: #c62828; }
.down { color: #2e7d32; }
.neutral { color: #757575; }
.up-bg { background-color: #ffebee; }
.down-bg { background-color: #e8f5e9; }
.neutral-bg { background-color: #f5f5f5; }

/* ===== 标题栏 ===== */
.header {
  background: #1a3a6c;
  color: #ffffff;
  padding: 8px 12px;
  border-bottom: 1px solid #0d1f3a;
}
.header-title { font-size: 16px; font-weight: 700; letter-spacing: 0.3px; }
.header-sub { font-size: 11px; color: rgba(255,255,255,0.75); margin-top: 1px; }
.header-meta {
  font-size: 11px;
  color: rgba(255,255,255,0.7);
  margin-top: 4px;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.status-dot {
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  margin-right: 3px;
  vertical-align: middle;
}
.status-open { background: #4caf50; }
.status-close { background: #9e9e9e; }

/* ===== 指数映射区域 ===== */
.index-section { border-bottom: 1px solid #e0e0e0; }
.index-section-title {
  font-size: 11px;
  font-weight: 700;
  color: #1a3a6c;
  padding: 4px 12px;
  background: #f0f2f5;
  border-bottom: 1px solid #e0e0e0;
  letter-spacing: 0.5px;
}
.index-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
}
.index-cell {
  padding: 5px 4px;
  text-align: center;
  border-right: 1px solid #e8e8e8;
  border-bottom: 1px solid #e8e8e8;
}
.index-cell:nth-child(4n) { border-right: none; }
.index-us-label { font-size: 10px; color: #888; }
.index-us-name { font-size: 11px; font-weight: 600; }
.index-us-value {
  font-size: 12px;
  font-weight: 700;
  font-family: "SF Mono", "Segoe UI Mono", Consolas, "Liberation Mono", Menlo, monospace;
  margin-top: 1px;
}
.index-arrow {
  font-size: 9px;
  color: #bbb;
  margin: 1px 0;
  line-height: 1;
}
.index-a-name { font-size: 10px; color: #666; }
.index-a-value {
  font-size: 11px;
  font-weight: 600;
  font-family: "SF Mono", "Segoe UI Mono", Consolas, "Liberation Mono", Menlo, monospace;
  margin-top: 1px;
}

/* ===== 工具栏 ===== */
.toolbar {
  padding: 6px 10px;
  display: flex;
  gap: 6px;
  border-bottom: 1px solid #e0e0e0;
  background: #ffffff;
  align-items: center;
}
.toolbar input {
  flex: 1;
  border: 1px solid #d0d0d0;
  padding: 4px 7px;
  font-size: 12px;
  border-radius: 0;
  font-family: inherit;
  min-width: 0;
}
.toolbar select {
  border: 1px solid #d0d0d0;
  padding: 4px 5px;
  font-size: 12px;
  border-radius: 0;
  font-family: inherit;
  background: #fff;
}
.btn {
  padding: 4px 8px;
  font-size: 12px;
  border: 1px solid #ccc;
  background: #f5f5f5;
  cursor: pointer;
  border-radius: 0;
  font-family: inherit;
  white-space: nowrap;
}
.btn-primary {
  background: #1a3a6c;
  color: #fff;
  border-color: #1a3a6c;
}

/* ===== 统计摘要 ===== */
.summary {
  display: flex;
  padding: 5px 10px;
  gap: 5px;
  border-bottom: 1px solid #e0e0e0;
  background: #fafafa;
}
.summary-item {
  flex: 1;
  text-align: center;
  padding: 3px 2px;
  border: 1px solid #e0e0e0;
  background: #fff;
}
.summary-num {
  font-size: 14px;
  font-weight: 700;
  font-family: "SF Mono", Consolas, monospace;
}
.summary-label {
  font-size: 10px;
  color: #666;
  margin-top: 1px;
}

/* ===== 板块区域 ===== */
.sector-block { border-bottom: 1px solid #e0e0e0; }
.sector-block:last-child { border-bottom: none; }
.sector-header {
  background: #1a3a6c;
  color: #fff;
  padding: 4px 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.sector-name-row {
  display: flex;
  align-items: center;
  gap: 5px;
}
.sector-arrow {
  font-size: 10px;
  width: 12px;
  text-align: center;
}
.sector-name { font-size: 13px; font-weight: 700; }
.sector-pct {
  font-size: 13px;
  font-weight: 700;
  font-family: "SF Mono", Consolas, monospace;
}

/* ===== 数据表格（紧凑） ===== */
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.data-table thead th {
  background: #f5f5f5;
  font-size: 10px;
  font-weight: 600;
  color: #777;
  padding: 2px 5px;
  text-align: left;
  border-bottom: 1px solid #d0d0d0;
  white-space: nowrap;
}
.data-table tbody td {
  padding: 2px 5px;
  border-bottom: 1px solid #f0f0f0;
  vertical-align: middle;
  white-space: nowrap;
}
.data-table tbody tr:last-child td { border-bottom: none; }

.col-type { width: 20px; text-align: center; font-size: 9px; color: #999; padding-left: 6px; }
.col-code { width: 44px; font-family: monospace; font-size: 11px; }
.col-name { font-size: 12px; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.col-price { width: 52px; text-align: right; font-family: monospace; font-size: 11px; }
.col-change { width: 44px; text-align: right; font-family: monospace; font-size: 11px; }
.col-pct { width: 48px; text-align: right; font-family: monospace; font-size: 11px; font-weight: 600; }

.tr-a { background: #fafafa; }
.tr-a td { border-bottom-color: #ececec; }

/* ===== 底部 ===== */
.footer {
  font-size: 10px;
  color: #999;
  text-align: center;
  padding: 6px 10px;
  border-top: 1px solid #e0e0e0;
}

/* ===== 截图 overlay ===== */
#overlay {
  display: none;
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.85);
  z-index: 9999;
  justify-content: center;
  align-items: center;
  flex-direction: column;
  padding: 20px;
}
#preview {
  width: 100%;
  max-width: 400px;
  background: #fff;
  overflow: auto;
  max-height: 80vh;
}
#preview img { width: 100%; display: block; }

/* ===== 打印 ===== */
@media print {
  body { background: #fff; color: #000; }
  .toolbar, .btn { display: none; }
  .sector-block { break-inside: avoid; page-break-inside: avoid; }
}

/* ===== 滚动条 ===== */
::-webkit-scrollbar { width: 2px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #ccc; }
</style>
</head>
<body>

<div id="capture-area">

  <!-- 标题栏 -->
  <div class="header">
    <div class="header-title">美股-A股映射雷达</div>
    <div class="header-sub">美股当夜涨跌 → A股次日指引</div>
    <div class="header-meta" id="marketStatus"></div>
  </div>

  <!-- 指数映射 -->
  <div class="index-section">
    <div class="index-section-title">指数映射</div>
    <div class="index-grid" id="indexGrid"></div>
  </div>

  <!-- 工具栏 -->
  <div class="toolbar">
    <input id="searchInput" type="text" placeholder="搜索板块 / 代码 / 名称">
    <select id="filterSelect">
      <option value="all">全部</option>
      <option value="positive">利好</option>
      <option value="neutral">中性</option>
      <option value="negative">利空</option>
    </select>
    <button class="btn btn-primary" onclick="takeScreenshot()">📸 截图</button>
    <button class="btn" onclick="window.print()">🖨️</button>
  </div>

  <!-- 统计摘要 -->
  <div class="summary" id="summary"></div>

  <!-- 板块列表 -->
  <div class="main-content" id="sectorsList"></div>

  <!-- 更新时间 -->
  <div class="footer" id="footer"></div>

</div>

<!-- 截图预览 -->
<div id="overlay" onclick="if(event.target===this) this.style.display='none'">
  <div id="preview"></div>
</div>

<script>
/* ===== 配置 ===== */
const INDICES = [
  { usTicker: '^SOX', usName: 'SOX', usLabel: '费城半导体', aCode: 'sh512480', aName: '半导体ETF', aLabel: '半导体' },
  { usTicker: '^NDX', usName: 'NDX', usLabel: '纳指100', aCode: 'sz399006', aName: '创业板指', aLabel: '创业板' },
  { usTicker: '^GSPC', usName: 'SPX', usLabel: '标普500', aCode: 'sh000300', aName: '沪深300', aLabel: '沪深300' },
  { usTicker: '^DJI', usName: 'DJI', usLabel: '道琼斯', aCode: 'sh000001', aName: '上证指数', aLabel: '上证' },
];

let DATA = null;
let INDEX_CACHE = {};

/* ===== 工具函数 ===== */
const fmt = n => n == null || isNaN(n) ? '—' : (n > 0 ? '+' : '') + n.toFixed(2);
const fmtPct = n => n == null || isNaN(n) ? '—' : (n > 0 ? '+' : '') + n.toFixed(2) + '%';
const cls = n => n == null || isNaN(n) ? 'neutral' : n > 0 ? 'up' : n < 0 ? 'down' : 'neutral';
const arrow = n => n == null || isNaN(n) ? '—' : n > 0 ? '▲' : n < 0 ? '▼' : '—';

/* ===== 加载主数据 ===== */
async function load() {
  try {
    const r = await fetch('/api/data');
    DATA = await r.json();
    render();
  } catch (e) {
    console.error('主数据加载失败:', e);
  }
}

/* ===== 加载指数数据（前端独立获取） ===== */
async function loadIndices() {
  // 优先使用后端返回的指数数据
  if (DATA && DATA.indices) {
    INDEX_CACHE = {};
    for (const idx of INDICES) {
      const us = DATA.indices[idx.usTicker];
      const a = DATA.indices[idx.aCode];
      if (us && us.status === 'ok') {
        INDEX_CACHE[idx.usTicker] = { price: us.price, change: us.change, changePercent: us.changePercent };
      }
      if (a && a.status === 'ok') {
        INDEX_CACHE[idx.aCode] = { price: a.price, change: a.change, changePercent: a.changePercent };
      }
    }
    renderIndices();
    return;
  }

  // Fallback: 前端独立获取
  // 美股指数（Yahoo Finance）
  for (const idx of INDICES) {
    try {
      const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(idx.usTicker)}?interval=1d&range=2d`;
      const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
      const data = await r.json();
      const result = data.chart?.result?.[0];
      if (result) {
        const close = result.indicators.quote[0].close;
        if (close && close.length >= 2 && close[close.length-1] != null && close[close.length-2] != null) {
          const cur = close[close.length-1];
          const prev = close[close.length-2];
          const change = cur - prev;
          const pct = change / prev * 100;
          INDEX_CACHE[idx.usTicker] = { price: cur, change, changePercent: pct };
        }
      }
    } catch (e) { /* 静默失败 */ }
  }

  // A股指数（腾讯行情）
  try {
    const qlist = INDICES.map(i => i.aCode).join(',');
    const url = `http://qt.gtimg.cn/q=${qlist}`;
    const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    const text = await r.text();
    for (const line of text.split(';')) {
      const m = line.match(/v_(\w+)="(.+)"/);
      if (!m) continue;
      const fields = m[2].split('~');
      if (fields.length < 5) continue;
      const code = fields[2];
      const price = parseFloat(fields[3]);
      const prev = parseFloat(fields[4]);
      if (isNaN(price) || isNaN(prev)) continue;
      const change = price - prev;
      const pct = prev ? (change / prev) * 100 : 0;
      INDEX_CACHE[code] = { price, prevClose: prev, change, changePercent: pct };
    }
  } catch (e) { /* 静默失败 */ }

  renderIndices();
}


/* ===== 渲染指数区域 ===== */
function renderIndices() {
  const grid = document.getElementById('indexGrid');
  grid.innerHTML = INDICES.map(idx => {
    const us = INDEX_CACHE[idx.usTicker] || {};
    const a = INDEX_CACHE[idx.aCode] || {};
    const usCls = cls(us.changePercent);
    const aCls = cls(a.changePercent);
    return `
      <div class="index-cell">
        <div class="index-us-label">${idx.usLabel}</div>
        <div class="index-us-name">${idx.usName}</div>
        <div class="index-us-value ${usCls}">${fmtPct(us.changePercent)}</div>
        <div class="index-arrow">↔</div>
        <div class="index-a-name">${idx.aLabel}</div>
        <div class="index-a-value ${aCls}">${fmtPct(a.changePercent)}</div>
      </div>
    `;
  }).join('');
}

/* ===== 渲染主内容 ===== */
function render() {
  if (!DATA) return;

  const m = DATA.marketStatus || {};
  const timeStr = DATA.updatedAt ? new Date(DATA.updatedAt).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';

  // 市场状态
  document.getElementById('marketStatus').innerHTML = `
    <span><span class="status-dot ${m.isUSOpen ? 'status-open' : 'status-close'}"></span>${m.us || '美股'}</span>
    <span><span class="status-dot ${m.isAOpen ? 'status-open' : 'status-close'}"></span>${m.a || 'A股'}</span>
    <span>${timeStr || m.time || ''}</span>
  `;

  // 统计摘要
  const s = DATA.sectors || [];
  const pos = s.filter(x => x.guidance.level === 'strong_positive' || x.guidance.level === 'positive').length;
  const neu = s.filter(x => x.guidance.level === 'neutral' || x.guidance.level === 'unknown').length;
  const neg = s.filter(x => x.guidance.level === 'negative' || x.guidance.level === 'strong_negative').length;
  document.getElementById('summary').innerHTML = [
    { l: '利好', v: pos, c: 'up' },
    { l: '中性', v: neu, c: 'neutral' },
    { l: '利空', v: neg, c: 'down' }
  ].map(x => `
    <div class="summary-item">
      <div class="summary-num ${x.c}">${x.v}</div>
      <div class="summary-label">${x.l}</div>
    </div>
  `).join('');

  // 筛选
  const q = (document.getElementById('searchInput').value || '').toLowerCase().trim();
  const f = document.getElementById('filterSelect').value;
  const filtered = s.filter(sector => {
    const mq = !q ||
      sector.name.toLowerCase().includes(q) ||
      sector.nameEn.toLowerCase().includes(q) ||
      sector.usLeaders.some(u => u.name.toLowerCase().includes(q) || u.ticker.toLowerCase().includes(q)) ||
      sector.aTargets.some(a => a.name.toLowerCase().includes(q) || a.code.includes(q));
    let mf = true;
    if (f === 'positive') mf = sector.guidance.level === 'strong_positive' || sector.guidance.level === 'positive';
    else if (f === 'neutral') mf = sector.guidance.level === 'neutral' || sector.guidance.level === 'unknown';
    else if (f === 'negative') mf = sector.guidance.level === 'negative' || sector.guidance.level === 'strong_negative';
    return mq && mf;
  });

  // 渲染板块
  document.getElementById('sectorsList').innerHTML = filtered.map(sector => {
    const wp = sector.usWeightedPct;
    const wpStr = wp == null ? '—' : fmtPct(wp);
    const wpCls = cls(wp);
    const sigArrow = arrow(wp);

    const rows = [];
    // 美股龙头
    for (const u of sector.usLeaders) {
      const c = u.changePercent;
      const hasErr = u.error || u.status !== 'ok';
      rows.push(`
        <tr class="tr-us">
          <td class="col-type">美</td>
          <td class="col-code">${u.ticker}</td>
          <td class="col-name">${u.name}</td>
          <td class="col-price">${hasErr ? '—' : (u.price != null ? u.price.toFixed(2) : '—')}</td>
          <td class="col-change ${hasErr ? 'neutral' : cls(c)}">${hasErr ? '—' : fmt(c)}</td>
          <td class="col-pct ${hasErr ? 'neutral' : cls(c)}">${hasErr ? '—' : fmtPct(c)}</td>
        </tr>
      `);
    }
    // A股映射
    for (const a of sector.aTargets) {
      const c = a.changePercent;
      const hasErr = a.error || a.status !== 'ok';
      rows.push(`
        <tr class="tr-a">
          <td class="col-type">A</td>
          <td class="col-code">${a.code}</td>
          <td class="col-name">${a.name}</td>
          <td class="col-price">${hasErr ? '—' : (a.price != null ? a.price.toFixed(2) : '—')}</td>
          <td class="col-change ${hasErr ? 'neutral' : cls(c)}">${hasErr ? '—' : fmt(c)}</td>
          <td class="col-pct ${hasErr ? 'neutral' : cls(c)}">${hasErr ? '—' : fmtPct(c)}</td>
        </tr>
      `);
    }

    return `
      <div class="sector-block">
        <div class="sector-header">
          <div class="sector-name-row">
            <span class="sector-arrow ${wpCls}">${sigArrow}</span>
            <span class="sector-name">${sector.name}</span>
          </div>
          <div class="sector-pct ${wpCls}">${wpStr}</div>
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th class="col-type"></th>
              <th class="col-code">代码</th>
              <th class="col-name">名称</th>
              <th class="col-price">价格</th>
              <th class="col-change">涨跌</th>
              <th class="col-pct">幅%</th>
            </tr>
          </thead>
          <tbody>${rows.join('')}</tbody>
        </table>
      </div>
    `;
  }).join('');

  // 底部更新时间
  const total = filtered.length;
  document.getElementById('footer').textContent = `共 ${total} 个板块 · 数据更新时间 ${timeStr || m.time || '—'}`;
}

/* ===== 截图导出 ===== */
function takeScreenshot() {
  const overlay = document.getElementById('overlay');
  const preview = document.getElementById('preview');
  overlay.style.display = 'none';

  html2canvas(document.getElementById('capture-area'), {
    scale: 2,
    useCORS: true,
    logging: false,
    backgroundColor: '#ffffff',
    windowWidth: document.getElementById('capture-area').scrollWidth,
    windowHeight: document.getElementById('capture-area').scrollHeight
  }).then(canvas => {
    overlay.style.display = 'flex';
    const img = document.createElement('img');
    img.src = canvas.toDataURL('image/png');
    preview.innerHTML = '';
    preview.appendChild(img);

    const dl = document.createElement('a');
    dl.href = canvas.toDataURL('image/png');
    dl.download = '美股A股雷达_' + new Date().toISOString().slice(0, 10) + '.png';
    dl.style = 'display:block;text-align:center;margin-top:10px;padding:8px;background:#1a3a6c;color:#fff;text-decoration:none;font-size:13px;cursor:pointer;';
    dl.innerText = '⬇️ 下载图片';
    preview.appendChild(dl);
  }).catch(err => {
    console.error('截图失败:', err);
    alert('截图失败，请尝试直接打印或截图');
  });
}

/* ===== 初始化 ===== */
load();
loadIndices();
setInterval(() => { load(); loadIndices(); }, 30000);

document.getElementById('searchInput').addEventListener('input', render);
document.getElementById('filterSelect').addEventListener('change', render);
</script>

</body>
</html>
'''

if __name__ == "__main__":
    try:
        fetcher.refresh()
    except Exception as e:
        print(f"[WARN] 首次刷新: {e}")
    threading.Thread(target=_bg, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"[INFO] 启动 http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
