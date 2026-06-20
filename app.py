#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股-A股映射雷达站 v6 — 极高密度·28板块·4列网格·红色主题·专业版
"""
import json, os, time, re, threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
from flask import Flask, jsonify, render_template_string

APP_DIR = Path(__file__).parent
REFRESH_INTERVAL = 300

with open(APP_DIR / "mapping.json", "r", encoding="utf-8") as f:
    MAPPING = json.load(f)

INDEX_CONFIG = [
    {"usTicker": "^SOX", "usName": "SOX", "usLabel": "费城半导体", "aCode": "sh000688", "aName": "科创50", "aLabel": "科创50"},
    {"usTicker": "^IXIC", "usName": "NASDAQ", "usLabel": "纳斯达克", "aCode": "sz399006", "aName": "创业板指", "aLabel": "创业板"},
    {"usTicker": "^GSPC", "usName": "SPX", "usLabel": "标普500", "aCode": "sh000300", "aName": "沪深300", "aLabel": "沪深300"},
    {"usTicker": "^DJI", "usName": "DJI", "usLabel": "道琼斯", "aCode": "sh000001", "aName": "上证指数", "aLabel": "上证"},
]

class Fetcher:
    def __init__(self):
        self.cache = {"usDate": "", "aDate": "", "indices": [], "sectors": []}
        self.lock = threading.Lock()
        self.headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    def _fetch_us(self, tickers):
        """使用新浪财经获取美股数据（替代Yahoo Finance）"""
        # 新浪ticker映射: Yahoo符号 -> 新浪格式
        sina_map = {
            "^SOX": "sox", "^IXIC": "ixic", "^DJI": "dji",
            "^GSPC": "inx", "^NDX": "ndx",
        }
        results = {}
        # 批量请求，新浪支持大批量
        batch = [f"gb_{sina_map.get(t, t.lower())}" for t in tickers]
        url = f"https://hq.sinajs.cn/list={','.join(batch)}"
        try:
            r = requests.get(url, headers={"Referer": "https://finance.sina.com.cn", **self.headers}, timeout=15)
            text = r.content.decode("gbk", errors="ignore")
            for line in text.split(";"):
                line = line.strip()
                if not line or not line.startswith("var hq_str_"):
                    continue
                m = re.search(r'var hq_str_gb_(\w+)="(.+)"', line)
                if not m:
                    continue
                sina_code = m.group(1)
                fields = m.group(2).split(",")
                if len(fields) < 3 or not fields[1]:
                    continue
                # 反向查找原始ticker
                orig_ticker = None
                for t in tickers:
                    expected = sina_map.get(t, t.lower())
                    if expected == sina_code:
                        orig_ticker = t
                        break
                if not orig_ticker:
                    continue
                price = float(fields[1])
                chg_pct = float(fields[2])
                # 计算昨收: price / (1 + chg_pct/100)
                prev = price / (1 + chg_pct / 100) if chg_pct != -100 else price
                results[orig_ticker] = {
                    "price": round(price, 2),
                    "prevClose": round(prev, 2),
                    "change": round(price - prev, 2),
                    "changePercent": round(chg_pct, 2),
                    "status": "ok"
                }
        except Exception as e:
            print(f"[WARN] 新浪美股获取失败: {e}")
            # fallback: 逐个返回空
            for t in tickers:
                if t not in results:
                    results[t] = {"error": str(e)}
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
        indices = {}
        us_tickers = [idx["usTicker"] for idx in INDEX_CONFIG]
        us_data = self._fetch_us(us_tickers)
        for idx in INDEX_CONFIG:
            t = idx["usTicker"]
            info = us_data.get(t, {})
            if info.get("status") == "ok":
                indices[t] = {"price": info["price"], "change": info["change"], "changePercent": info["changePercent"], "status": "ok"}
        a_codes = [{"market": idx["aCode"][:2].upper(), "code": idx["aCode"][2:]} for idx in INDEX_CONFIG]
        a_data = self._fetch_a(a_codes)
        for idx in INDEX_CONFIG:
            ac = idx["aCode"][2:]
            info = a_data.get(ac, {})
            if info.get("status") == "ok":
                indices[idx["aCode"]] = {"price": info["price"], "change": info["change"], "changePercent": info["changePercent"], "status": "ok"}
        return indices

    def refresh(self):
        print(f"[{datetime.now()}] 开始刷新...")
        all_us = set()
        all_a = []
        for s in MAPPING["sectors"]:
            for u in s.get("usLeaders", []):
                all_us.add(u["ticker"])
            for a in s.get("aTargets", []):
                all_a.append(a)
        seen = set()
        uniq_a = [s for s in all_a if not (s["code"] in seen or seen.add(s["code"]))]
        us_data = self._fetch_us(list(all_us))
        a_data = self._fetch_a(uniq_a)
        indices_data = self._fetch_indices()

        now = datetime.now(timezone(timedelta(hours=8)))
        weekday = now.weekday()  # 0=周一, 5=周六, 6=周日

        if weekday == 0:  # 周一：最新数据均为上周五
            us_date = (now - timedelta(days=3)).strftime("%Y-%m-%d")
            a_date = us_date
            us_label = "周五收盘"
            a_label = "周五收盘"
        elif weekday in (5, 6):  # 周末：最新数据均为上周五
            us_date = (now - timedelta(days=weekday-4)).strftime("%Y-%m-%d")
            a_date = us_date
            us_label = "周五收盘"
            a_label = "周五收盘"
        else:  # 周二到周五
            us_date = now.strftime("%Y-%m-%d")
            a_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            us_label = "今日收盘"
            a_label = "昨日收盘"

        indices = []
        for idx in INDEX_CONFIG:
            us_info = indices_data.get(idx["usTicker"], {})
            a_info = indices_data.get(idx["aCode"], {})
            us_chg = us_info.get("changePercent", 0) if us_info.get("status") == "ok" else 0
            a_chg = a_info.get("changePercent", 0) if a_info.get("status") == "ok" else 0
            indices.append({
                "name": f"{idx['usName']} ↔ {idx['aLabel']}",
                "pair": f"{idx['usName']} ↔ {idx['aLabel']}",
                "chg": round(us_chg, 2),
                "aChg": round(a_chg, 2),
                "desc": f"{idx['usLabel']} vs A股{idx['aLabel']}"
            })

        sectors = []
        for sector in MAPPING["sectors"]:
            if sector.get("type") == "index":
                continue
            tw = sum(u.get("weight", 1) for u in sector.get("usLeaders", []))
            wp = 0.0
            us_stocks = []
            for u in sector.get("usLeaders", []):
                info = us_data.get(u["ticker"], {})
                p = info.get("changePercent")
                if p is not None:
                    wp += p * u.get("weight", 1)
                name = u.get("name", u["ticker"])
                us_stocks.append({
                    "code": u["ticker"],
                    "name": name,
                    "chg": round(info.get("changePercent", 0), 2) if info.get("status") == "ok" else 0
                })
            wp = wp / tw if tw else 0

            a_targets = sector.get("aTargets", [])
            # Core: 前3个固定不动
            core = a_targets[:3]
            # Pool: 后7个按当日涨幅排序
            pool = a_targets[3:]

            a_stocks = []
            for a in core:
                info = a_data.get(a["code"], {})
                a_stocks.append({
                    "code": a["code"],
                    "name": a["name"],
                    "chg": round(info.get("changePercent", 0), 2) if info.get("status") == "ok" else 0
                })

            pool_stocks = []
            for a in pool:
                info = a_data.get(a["code"], {})
                pool_stocks.append({
                    "code": a["code"],
                    "name": a["name"],
                    "chg": round(info.get("changePercent", 0), 2) if info.get("status") == "ok" else 0
                })
            pool_stocks.sort(key=lambda x: x["chg"], reverse=True)
            a_stocks = a_stocks + pool_stocks

            direction = "up" if wp >= 2 else "down" if wp <= -2 else "neutral"
            sectors.append({
                "name": sector["name"],
                "weightedChg": round(wp, 2),
                "direction": direction,
                "usStocks": us_stocks,
                "aStocks": a_stocks
            })

        sectors.sort(key=lambda x: x["weightedChg"], reverse=True)

        with self.lock:
            self.cache = {
                "usDate": us_date, "aDate": a_date,
                "usLabel": us_label, "aLabel": a_label,
                "indices": indices, "sectors": sectors
            }
        print(f"[{datetime.now()}] 刷新完成 板块={len(sectors)} 美股={len(us_data)} A股={len(a_data)} 指数={len(indices_data)}")

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
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>美股-A股映射雷达站</title>
<script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
<style>
/* ===== Reset & Base ===== */
*{margin:0;padding:0;box-sizing:border-box;font-family:system-ui,-apple-system,Segoe UI,Microsoft YaHei,SimHei,sans-serif;}
html,body{width:100%;min-height:100vh;background:#ffffff;}
body{font-size:8px;line-height:1;color:#1a1a1a;}

/* ===== Header ===== */
.page-header{
  background:linear-gradient(180deg,#b71c1c 0%,#8e0000 100%);
  color:#fff;padding:3px 5px;text-align:center;border-bottom:2px solid #7f0000;
}
.page-header h1{font-size:13px;font-weight:700;letter-spacing:0.5px;margin:0;line-height:1.3;}
.page-header .sub{font-size:8px;opacity:0.95;margin-top:1px;line-height:1.2;}
.page-header .time-bar{
  font-size:8px;margin-top:1px;opacity:0.9;
  display:flex;justify-content:center;gap:8px;flex-wrap:wrap;line-height:1.2;
}
.page-header .time-bar span{
  background:rgba(255,255,255,0.18);padding:1px 4px;border-radius:1px;white-space:nowrap;
}
.page-header .notice{
  font-size:8px;margin-top:1px;opacity:0.85;color:#ffebee;font-weight:600;
}

/* ===== Index Bar ===== */
.index-bar{
  display:grid;grid-template-columns:repeat(4,1fr);gap:2px;
  padding:2px 4px;background:#fafafa;border-bottom:1px solid #e0e0e0;
}
.index-card{
  border:1px solid #e0e0e0;background:#fff;padding:2px 2px;text-align:center;
}
.index-card .pair{font-size:8px;font-weight:700;color:#b71c1c;line-height:1.1;}
.index-card .name{font-size:7px;color:#616161;line-height:1.1;margin-top:1px;}
.index-card .chg{font-size:8px;font-weight:700;line-height:1.1;margin-top:1px;}
.index-card .chg.up{color:#c62828;}
.index-card .chg.down{color:#2e7d32;}
.index-card .a-chg{font-size:7px;color:#757575;line-height:1.1;margin-top:1px;}

/* ===== Summary ===== */
.summary-strip{
  display:flex;gap:2px;padding:2px 4px;background:#fff;
  border-bottom:1px solid #e0e0e0;font-size:8px;justify-content:center;flex-wrap:wrap;
}
.summary-strip .chip{
  padding:1px 3px;border:1px solid #ddd;background:#fff;white-space:nowrap;border-radius:1px;
}
.summary-strip .chip strong{font-size:8px;}
.summary-strip .chip.up{color:#c62828;border-color:#ffcdd2;background:#ffebee;}
.summary-strip .chip.mid{color:#757575;border-color:#e0e0e0;background:#fafafa;}
.summary-strip .chip.down{color:#2e7d32;border-color:#c8e6c9;background:#e8f5e9;}

/* ===== Controls ===== */
.controls{
  display:flex;gap:2px;padding:2px 4px;background:#f5f5f5;
  border-bottom:1px solid #e0e0e0;align-items:center;flex-wrap:wrap;
}
.controls input[type="text"]{
  flex:1;min-width:50px;font-size:8px;padding:1px 2px;
  border:1px solid #ccc;background:#fff;height:16px;
}
.controls select{
  font-size:8px;padding:1px 2px;border:1px solid #ccc;background:#fff;height:16px;
}
.controls button{
  font-size:8px;padding:1px 5px;
  border:1px solid #7f0000;background:#c62828;color:#fff;
  cursor:pointer;height:16px;font-weight:600;
}

/* ===== Sector Grid: 4 columns always (mobile + PC) ===== */
.sector-grid{
  display:grid;
  grid-template-columns:repeat(4,1fr);
  gap:2px;padding:2px 4px;
}

/* ===== Sector Block ===== */
.sector-block{
  border:1px solid #ddd;background:#fff;border-radius:2px;overflow:hidden;
  box-shadow:0 1px 2px rgba(0,0,0,0.05);
}
.sector-header{
  display:flex;align-items:center;justify-content:space-between;
  padding:1px 3px;background:#b71c1c;color:#fff;font-size:5px;font-weight:700;
  line-height:1.2;min-height:12px;
  letter-spacing:0.2px;
}
.sector-header .right{
  display:flex;align-items:center;gap:3px;font-size:6px;font-weight:400;
}
.sector-header .wchg{font-weight:700;}
.sector-header .mapdir{
  font-weight:700;padding:0 2px;border:1px solid rgba(255,255,255,0.5);
  font-size:6px;min-width:11px;text-align:center;
}

/* ===== Sector Body ===== */
.sector-body{
  padding:2px 4px;
}

/* US Row */
.us-section{
  font-size:7px;line-height:1.4;padding-bottom:2px;
  border-bottom:1px dashed #eee;margin-bottom:2px;
}
.us-title{
  color:#b71c1c;font-weight:700;font-size:6px;margin-bottom:1px;
}
.us-item{
  display:inline-block;margin-right:3px;white-space:nowrap;
}
.us-ticker{color:#b71c1c;font-weight:700;}
.us-name{color:#424242;}
.us-chg{font-weight:700;font-size:6px;}
.us-chg-up{color:#c62828;}
.us-chg-down{color:#2e7d32;}

/* A Row */
.a-section{
  font-size:7px;line-height:1.4;
}
.a-title{
  color:#1565c0;font-weight:700;font-size:7px;margin-bottom:1px;
}
.a-list{
  display:grid;
  grid-template-columns:repeat(2,1fr);
  gap:0 4px;
}
.a-item{
  white-space:nowrap;color:#333;font-size:7px;
}

/* Export Bar */
.export-bar{
  padding:2px 4px;background:#fafafa;border-top:1px solid #e0e0e0;
  display:flex;gap:2px;justify-content:center;flex-wrap:wrap;
}
.export-bar button{
  font-size:7px;padding:1px 6px;
  border:1px solid #7f0000;background:#c62828;color:#fff;
  cursor:pointer;font-weight:600;height:16px;
}
</style>
</head>
<body>

<!-- Header -->
<div class="page-header">
  <h1>美股昨夜涨跌 &rarr; A股今日映射指引</h1>
  <div class="sub">美股已收盘（T-1） / A股尚未开盘（映射基于A股前一日收盘）</div>
  <div class="time-bar" id="datesLine"></div>
  <div class="notice" id="noticeLine">A股数据为前一日收盘，按前一日涨幅从高到低排列</div>
</div>

<!-- Index Mapping -->
<div class="index-bar" id="indexBar"></div>

<!-- Summary -->
<div class="summary-strip" id="summaryStrip"></div>

<!-- Controls -->
<div class="controls" id="ctrlBar">
  <input type="text" id="searchInput" placeholder="搜索标的/代码/板块...">
  <select id="filterSelect">
    <option value="all">全部映射</option>
    <option value="up">▲ 映射利好</option>
    <option value="neutral">— 映射中性</option>
    <option value="down">▼ 映射利空</option>
  </select>
  <button onclick="doExport()">导出截图</button>
</div>

<!-- Sector Grid -->
<div class="sector-grid" id="sectorGrid"></div>

<!-- Export bar -->
<div class="export-bar" id="exportBar">
  <button onclick="doExport()">导出 PNG 截图</button>
</div>

<script>
/* ==================== Data ==================== */
let DATA = null;
async function loadData() {
  try {
    const r = await fetch('/api/data');
    DATA = await r.json();
    render();
  } catch(e) { console.error('API Error:', e); }
}

/* ==================== Helpers ==================== */
const fmtChg = (n) => (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
const fmtNum = (n) => n.toFixed(2);
const dirIcon = (d) => d === 'up' ? '▲' : d === 'down' ? '▼' : '—';
const dirColor = (d) => d === 'up' ? 'c62828' : d === 'down' ? '2e7d32' : '757575';
const dirClass = (d) => d > 0 ? 'chg-red' : d < 0 ? 'chg-green' : 'chg-gray';
const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
};
const yesterdayStr = () => {
  const d = new Date(); d.setDate(d.getDate()-1);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
};

/* ==================== Render ==================== */
function render(){
  if(!DATA) return;
  const usDate = DATA.usDate || todayStr();
  const aDate = DATA.aDate || yesterdayStr();
  const usLabel = DATA.usLabel || "已收盘";
  const aLabel = DATA.aLabel || "前一日收盘";

  // Dates
  document.getElementById('datesLine').innerHTML = `
    <span>美股${usLabel} · ${usDate}</span>
    <span>A股${aLabel} · ${aDate}</span>
  `;

  // Notice
  const isWeekend = usLabel === '周五收盘';
  document.getElementById('noticeLine').innerHTML = isWeekend
    ? '周末休市 | 美股/A股数据均为周五收盘，按涨幅从高到低排列'
    : 'A股尚未开盘，数据按前一日收盘从高到低排列';

  // Indices
  const idxHtml = (DATA.indices || []).map(idx => {
    const cls = idx.chg > 0 ? 'up' : idx.chg < 0 ? 'down' : '';
    const arr = idx.chg > 0 ? '▲' : idx.chg < 0 ? '▼' : '—';
    const aCls = idx.aChg > 0 ? 'up' : idx.aChg < 0 ? 'down' : '';
    const aArr = idx.aChg > 0 ? '▲' : idx.aChg < 0 ? '▼' : '—';
    return `
      <div class="index-card">
        <div class="pair">${idx.pair}</div>
        <div class="name">${idx.desc}</div>
        <div class="chg ${cls}">${arr} ${fmtChg(idx.chg)}</div>
        <div class="a-chg ${aCls}">A股前日 ${aArr}${fmtChg(idx.aChg)}</div>
      </div>
    `;
  }).join('');
  document.getElementById('indexBar').innerHTML = idxHtml;

  // Summary
  const counts = {up:0,neutral:0,down:0};
  (DATA.sectors || []).forEach(s=>{counts[s.direction]++;});
  document.getElementById('summaryStrip').innerHTML = `
    <div class="chip up"><strong>▲ ${counts.up}</strong> 映射利好</div>
    <div class="chip mid"><strong>— ${counts.neutral}</strong> 映射中性</div>
    <div class="chip down"><strong>▼ ${counts.down}</strong> 映射利空</div>
    <div class="chip">板块共 ${(DATA.sectors || []).length} 个</div>
    <div class="chip" style="color:#b71c1c;font-weight:700">A股数据为前一日收盘</div>
  `;

  // Sectors
  const search = document.getElementById('searchInput').value.trim().toLowerCase();
  const filter = document.getElementById('filterSelect').value;

  let filtered = (DATA.sectors || []);
  if(filter !== 'all') filtered = filtered.filter(s => s.direction === filter);
  if(search) {
    filtered = filtered.filter(s => {
      const hay = (s.name + ' ' + s.usStocks.map(x=>x.code+' '+x.name).join(' ') + ' ' + s.aStocks.map(x=>x.code+' '+x.name).join(' ')).toLowerCase();
      return hay.includes(search);
    });
  }

  const grid = document.getElementById('sectorGrid');
  grid.innerHTML = '';

  filtered.forEach(sec => {
    const dIcon = dirIcon(sec.direction);
    const wSign = sec.weightedChg >= 0 ? '+' : '';

    // US stocks: ticker·name(change)
    const usItems = sec.usStocks.map(st => {
      const chgCls = st.chg > 0 ? 'us-chg-up' : st.chg < 0 ? 'us-chg-down' : '';
      return `<span class="us-item"><span class="us-ticker">${st.code}</span><span class="us-name">${st.name.split(' / ')[1] || st.name}</span><span class="us-chg ${chgCls}">${fmtChg(st.chg)}</span></span>`;
    }).join('');

    // A stocks: just names (no codes, no changes)
    const aItems = sec.aStocks.map(st => {
      return `<span class="a-item">${st.name}</span>`;
    }).join('');

    const block = document.createElement('div');
    block.className = 'sector-block';
    block.innerHTML = `
      <div class="sector-header">
        <span>${sec.name}</span>
        <span class="right">
          <span class="wchg">${wSign}${fmtNum(sec.weightedChg)}%</span>
          <span class="mapdir" style="border-color:rgba(255,255,255,0.6)">${dIcon}</span>
        </span>
      </div>
      <div class="sector-body">
        <div class="us-section">
          <div class="us-title">美股</div>
          <div class="us-list">${usItems}</div>
        </div>
        <div class="a-section">
          <div class="a-title">A股映射</div>
          <div class="a-list">${aItems}</div>
        </div>
      </div>
    `;
    grid.appendChild(block);
  });
}

/* ==================== Export ==================== */
function doExport(){
  const bars = document.querySelectorAll('.controls, .export-bar');
  bars.forEach(b=>b.style.display='none');
  document.body.style.margin='0';

  html2canvas(document.body, {
    backgroundColor: '#ffffff',
    scale: 2,
    useCORS: true,
    allowTaint: false,
    logging: false
  }).then(canvas => {
    const link = document.createElement('a');
    link.download = `美A映射_${todayStr()}.png`;
    link.href = canvas.toDataURL('image/png');
    link.click();
    bars.forEach(b=>b.style.display='');
  }).catch(err => {
    console.error(err);
    alert('导出失败，请重试');
    bars.forEach(b=>b.style.display='');
  });
}

/* ==================== Events ==================== */
document.getElementById('searchInput').addEventListener('input', render);
document.getElementById('filterSelect').addEventListener('change', render);

/* ==================== Init ==================== */
loadData();
setInterval(loadData, 30000);
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
    port = int(os.environ.get("PORT", 5002))
    print(f"[INFO] 启动 http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, threaded=True)
