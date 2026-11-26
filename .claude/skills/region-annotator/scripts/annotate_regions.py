# -*- coding: utf-8 -*-
"""把项目识别区域清单画到用户提供的游戏截图上，并生成可交互 HTML。

清单：../references/regions.json（窗口相对坐标，基准 1378x831）。
用法（仓库根，项目 venv）：

    python .claude/skills/region-annotator/scripts/annotate_regions.py \
        --image <截图路径> [--out logs/region_maps] [--groups main,windows]
        [--format both|png|html]

输出：PNG（每组一张，编号框 + 图例）+ 00_识别区域交互.html
（自包含：截图内嵌 base64；框/条目点击联动、悬停高亮、分类筛选、搜索）。
规则：全屏识别不画框（文字说明）；动态区域只写进说明；条件界面用虚线。
"""
import argparse
import base64
import io
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve()
SKILL_DIR = HERE.parents[1]
DEFAULT_MANIFEST = SKILL_DIR / "references" / "regions.json"


def find_repo_root():
    for p in HERE.parents:
        if (p / "src" / "device" / "map.py").exists():
            return p
    return Path.cwd()


REPO = find_repo_root()


def hex_rgb(s):
    s = (s or "").lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def load_font(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(str(p), size)
        except Exception:
            continue
    return ImageFont.load_default()


def clamp(rect, w, h):
    l, t, r, b = rect
    return (max(0, int(l)), max(0, int(t)), min(w, int(r)), min(h, int(b)))


def chip(d, xy, num, color, font):
    x, y = xy
    txt = str(num)
    bw = max(18, font.getbbox(txt)[2] + 8)
    d.rounded_rectangle([x, y, x + bw, y + 16], radius=3,
                        fill=(10, 10, 10, 210),
                        outline=color + (255,), width=1)
    d.text((x + 4, y), txt, font=font, fill=color + (255,))


def dashed_line(d, p1, p2, color, width=2, dash=9, gap=6):
    x1, y1 = p1
    x2, y2 = p2
    length = max(abs(x2 - x1), abs(y2 - y1))
    if length == 0:
        return
    steps = int(length / (dash + gap)) + 1
    for i in range(steps):
        a = i * (dash + gap) / length
        b = min(1.0, (i * (dash + gap) + dash) / length)
        d.line([(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a),
                (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)],
               fill=color, width=width)


def dashed_rect(d, rect, color, width=2):
    l, t, r, b = rect
    dashed_line(d, (l, t), (r, t), color, width)
    dashed_line(d, (l, b), (r, b), color, width)
    dashed_line(d, (l, t), (l, b), color, width)
    dashed_line(d, (r, t), (r, b), color, width)


def text_block(img, xy, lines, font, bg=(0, 0, 0, 185), pad=6):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    line_h = font.getbbox("国")[3] + 5
    w = max(font.getbbox(s)[2] for s in lines) + pad * 2
    h = line_h * len(lines) + pad * 2
    x, y = xy
    d.rectangle([x, y, x + w, y + h], fill=bg)
    for i, s in enumerate(lines):
        d.text((x + pad, y + pad + i * line_h), s, font=font,
               fill=(255, 255, 255))
    return Image.alpha_composite(img, layer)


def wrap_text(text, font, max_w):
    lines, cur = [], ""
    for ch in text:
        if cur and font.getbbox(cur + ch)[2] > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines or [""]


def scale_region(reg, sx, sy):
    out = dict(reg)
    out["rect"] = [int(reg["rect"][0] * sx), int(reg["rect"][1] * sy),
                   int(reg["rect"][2] * sx), int(reg["rect"][3] * sy)]
    if reg.get("chip"):
        out["chip"] = [int(reg["chip"][0] * sx), int(reg["chip"][1] * sy)]
    return out


# ==================== PNG ====================

def render_group(group, src, manifest, out_dir, warnings, scale_note=None):
    W, H = src.size
    base = src
    if group.get("dim"):
        overlay = Image.new("RGB", src.size, (0, 0, 0))
        base = Image.blend(src, overlay, float(group["dim"]))
    img = base.convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cat_colors = {k: hex_rgb(v["color"])
                  for k, v in manifest["categories"].items()}
    font_reg = [manifest["fonts"]["regular"],
                *manifest["fonts"].get("fallbacks", [])]
    font_bold = [manifest["fonts"]["bold"],
                 *manifest["fonts"].get("fallbacks", [])]
    f_num = load_font(font_reg, 12)
    f_on = load_font(font_reg, 13)

    for reg in group.get("regions", []):
        color = cat_colors.get(reg.get("cat"), (200, 200, 200))
        l, t, r, b = clamp(reg["rect"], W, H)
        if r <= l or b <= t:
            warnings.append(f"{group['key']}#{reg.get('num')} 区域越界/为空")
            continue
        if group.get("solid", True):
            d.rectangle([l, t, r, b], fill=color + (34,),
                        outline=color + (235,), width=2)
        else:
            dashed_rect(d, (l, t, r, b), color + (235,), width=2)
        chip_xy = reg.get("chip") or (l + 2, t + 2)
        chip(d, (max(0, min(chip_xy[0], W - 20)),
                 max(0, min(chip_xy[1], H - 18))),
             reg.get("num", "?"), color, f_num)

    img = Image.alpha_composite(img, layer)

    for note in group.get("on_image_notes", []):
        img = text_block(img, tuple(note["xy"]), note["lines"], f_on)

    f_title = load_font(font_bold, 18)
    f_sub = load_font(font_reg, 12)
    f_leg = load_font(font_reg, 12)
    f_pur = load_font(font_reg, 11)
    line_h = 33
    cols = 2 if W >= 1100 else 1
    regions = group.get("regions", [])
    rows = math.ceil(len(regions) / cols) if regions else 0
    note_lines = []
    for n in group.get("fullscreen_notes", []):
        for ln in wrap_text("★ " + n, f_sub, W - 40):
            note_lines.append((ln, (255, 235, 59)))
    for n in group.get("legend_notes", []):
        for ln in wrap_text("• " + n, f_sub, W - 40):
            note_lines.append((ln, (200, 200, 200)))
    if scale_note:
        for ln in wrap_text("• " + scale_note, f_sub, W - 40):
            note_lines.append((ln, (255, 170, 120)))
    note_h = 19
    panel_h = 56 + rows * line_h + len(note_lines) * note_h + 20
    out = Image.new("RGB", (W, H + panel_h), (26, 26, 30))
    out.paste(img.convert("RGB"), (0, 0))
    d2 = ImageDraw.Draw(out)
    d2.text((14, H + 8), group.get("title", ""), font=f_title,
            fill=(255, 255, 255))
    d2.text((14, H + 32), group.get("subtitle", ""), font=f_sub,
            fill=(170, 170, 170))

    col_w = (W - 30) // cols
    top = H + 54
    for i, reg in enumerate(regions):
        color = cat_colors.get(reg.get("cat"), (200, 200, 200))
        c = i // rows
        row = i % rows
        x = 14 + c * col_w
        y = top + row * line_h
        d2.rectangle([x, y + 2, x + 12, y + 14], fill=color)
        head = f"{reg.get('num', '?'):>2} {reg.get('name', '')}"
        d2.text((x + 18, y), head, font=f_leg, fill=(235, 235, 235))
        coord = "(" + ",".join(str(v) for v in reg["rect"]) + ")"
        d2.text((x + 18 + f_leg.getbbox(head)[2] + 8, y), coord,
                font=f_leg, fill=(150, 190, 240))
        d2.text((x + 32, y + 16), reg.get("purpose", ""), font=f_pur,
                fill=(155, 155, 155))

    y = top + rows * line_h + 8
    for text, color in note_lines:
        d2.text((14, y), text, font=f_sub, fill=color)
        y += note_h

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / group.get("file", f"{group['key']}.png")
    out.save(path)
    return path


# ==================== HTML ====================

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>识别区域交互标注</title>
<style>
:root{--iw:__W__;--ih:__H__;--bg:#111318;--panel:#1b1e25;--line:#2b2f39;
  --text:#e9eaee;--muted:#98a0ad;}
*{box-sizing:border-box;}
html,body{height:100%;}
body{margin:0;background:var(--bg);color:var(--text);display:flex;
  flex-direction:column;font-family:"Microsoft YaHei","PingFang SC",sans-serif;}
header{display:flex;align-items:center;gap:14px;padding:10px 16px;
  border-bottom:1px solid var(--line);background:#151820;flex-wrap:wrap;}
h1{font-size:15px;margin:0;white-space:nowrap;}
.sub{font-size:11px;color:var(--muted);margin-top:2px;}
.tabs{display:flex;gap:6px;}
.tab{background:#20242e;color:#c9cdd6;border:1px solid var(--line);
  border-radius:999px;padding:5px 12px;font-size:12px;cursor:pointer;}
.tab.on{background:#2d3648;border-color:#5b6b8c;color:#fff;}
.spacer{flex:1;}
.toggle{font-size:12px;color:var(--muted);display:flex;align-items:center;
  gap:5px;cursor:pointer;}
.layout{flex:1;display:flex;min-height:0;}
.stage-wrap{flex:1;overflow:auto;padding:12px;background:#0b0d11;
  display:flex;justify-content:center;align-items:flex-start;}
.stage{position:relative;flex:none;
  width:min(100%, calc((100vh - 96px) * var(--iw) / var(--ih)));}
.stage img{width:100%;display:block;border-radius:4px;
  box-shadow:0 6px 26px rgba(0,0,0,.5);user-select:none;-webkit-user-drag:none;}
.layer{position:absolute;inset:0;}
.region{position:absolute;border:2px solid;border-radius:2px;cursor:pointer;
  transition:opacity .12s,box-shadow .12s;}
.region .num{position:absolute;left:-2px;top:-20px;font-size:11px;
  font-weight:700;background:rgba(8,9,12,.88);border:1px solid;
  border-radius:3px;padding:0 4px;line-height:16px;white-space:nowrap;}
.region.numin .num{top:1px;left:1px;}
.region.hover{outline:2px solid #fff;outline-offset:1px;z-index:20;}
.region.active{z-index:25;border-width:3px;
  box-shadow:0 0 0 2px #fff,0 0 18px var(--glow);
  background:var(--hi)!important;}
.layer.has-active .region:not(.active){opacity:.15;}
.hidden{display:none!important;}
.onnote{position:absolute;background:rgba(8,10,14,.82);border:1px solid #3a4150;
  border-radius:6px;padding:6px 8px;font-size:11px;line-height:16px;
  color:#dfe4ec;max-width:42%;pointer-events:none;
  box-shadow:0 4px 14px rgba(0,0,0,.35);}
.side{width:430px;flex:none;border-left:1px solid var(--line);
  display:flex;flex-direction:column;background:var(--panel);min-height:0;}
.tools{padding:10px 12px;border-bottom:1px solid var(--line);}
#search{width:100%;background:#12151c;border:1px solid var(--line);
  border-radius:6px;color:var(--text);padding:7px 9px;font-size:12px;
  outline:none;}
#search:focus{border-color:#5b6b8c;}
.cats{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px;}
.cat-chip{display:flex;align-items:center;gap:4px;background:#20242e;
  border:1px solid var(--line);color:#aeb4c0;border-radius:999px;
  padding:3px 8px;font-size:11px;cursor:pointer;}
.cat-chip.on{color:#fff;border-color:#55627d;background:#2a3140;}
.dot{width:9px;height:9px;border-radius:2px;display:inline-block;flex:none;}
.mini-actions{display:flex;gap:10px;margin-top:6px;font-size:11px;}
.mini-actions button{background:none;border:none;color:#8fa0c0;cursor:pointer;
  padding:0;font-size:11px;text-decoration:underline;}
.info{padding:10px 12px;border-bottom:1px solid var(--line);font-size:12px;
  line-height:18px;min-height:64px;background:#161922;color:#c9cdd6;}
.info-title{font-size:13px;font-weight:700;color:#fff;margin-bottom:3px;
  display:flex;align-items:center;gap:6px;}
.info-meta{color:var(--muted);font-size:11px;margin-bottom:4px;}
.kv{color:#aab2c0;font-size:11.5px;}
.kv b{color:#e8ecf4;font-weight:600;}
.list{flex:1;overflow:auto;padding:6px 8px 20px;}
.cat-head{display:flex;align-items:center;gap:6px;font-size:11px;
  color:#8d96a6;margin:10px 4px 4px;}
.item{display:flex;gap:8px;padding:6px 8px;border-radius:6px;
  cursor:pointer;border:1px solid transparent;}
.item:hover,.item.hover{background:#232936;}
.item.active{background:#283248;border-color:#4b5d80;}
.item .txt{min-width:0;}
.item .nm{display:block;font-size:12.5px;color:#e7e9ee;}
.item .meta{display:block;font-size:11px;color:#8f98a8;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;}
.notes{max-height:34%;overflow:auto;border-top:1px solid var(--line);
  padding:8px 12px 14px;font-size:11px;line-height:17px;color:#aab2c0;
  background:#161922;}
.note-h{color:#e6c766;font-weight:700;margin:8px 0 3px;}
.note.warn{color:#ffb06b;}
#tip{position:fixed;display:none;z-index:99;background:rgba(8,10,14,.95);
  border:1px solid #3a4150;border-radius:6px;padding:6px 9px;font-size:11px;
  color:#e5e9f0;pointer-events:none;max-width:340px;line-height:16px;}
@media (max-width:1100px){
  .layout{flex-direction:column;}
  .side{width:auto;border-left:none;border-top:1px solid var(--line);
    max-height:46vh;}
  .stage{width:100%;}
}
</style>
</head>
<body>
<header>
  <div>
    <h1>识别区域交互标注</h1>
    <div class="sub" id="sub"></div>
  </div>
  <div class="tabs" id="tabs"></div>
  <div class="spacer"></div>
  <label class="toggle"><input type="checkbox" id="notesToggle" checked>
    显示全屏说明</label>
</header>
<div class="layout">
  <div class="stage-wrap">
    <div class="stage" id="stage">
      <img id="shot" src="__IMG_URI__" alt="游戏截图">
      <div class="layer" id="layer"></div>
    </div>
  </div>
  <aside class="side">
    <div class="tools">
      <input id="search" placeholder="搜索区域名称 / 用途 / 坐标">
      <div class="cats" id="cats"></div>
      <div class="mini-actions">
        <button id="catAll">全选分类</button>
        <button id="catNone">清空分类</button>
        <button id="clearSel">取消选中</button>
      </div>
    </div>
    <div class="info" id="info">点击图中的框或右侧条目查看详情</div>
    <div class="list" id="list"></div>
    <div class="notes" id="notes"></div>
  </aside>
</div>
<div id="tip"></div>
<script id="data" type="application/json">__DATA_JSON__</script>
<script>
(function(){
  var DATA = JSON.parse(document.getElementById('data').textContent);
  var CATS = DATA.categories || {};
  var CAT_KEYS = Object.keys(CATS);
  DATA.groups.forEach(function(g){
    (g.regions || []).forEach(function(r){ r.id = g.key + ':' + r.num; });
  });
  var state = { group: DATA.groups.length ? DATA.groups[0].key : null,
                cats: new Set(CAT_KEYS), q: '', active: null, notes: true };
  var $ = function(id){ return document.getElementById(id); };
  var stage = $('stage'), layer = $('layer'), listEl = $('list');
  var infoEl = $('info'), tipEl = $('tip'), noteToggle = $('notesToggle');

  function cur(){ return DATA.groups.find(function(g){
    return g.key === state.group; }); }
  function colorOf(c){ return (CATS[c] || {}).color || '#999'; }
  function labelOf(c){ return (CATS[c] || {}).label || c; }
  function hexA(hex, a){
    var h = hex.replace('#','');
    return 'rgba(' + parseInt(h.substr(0,2),16) + ',' +
           parseInt(h.substr(2,2),16) + ',' +
           parseInt(h.substr(4,2),16) + ',' + a + ')';
  }
  function esc(s){ return String(s == null ? '' : s).replace(
    /[&<>"']/g, function(ch){ return {'&':'&amp;','<':'&lt;','>':'&gt;',
      '"':'&quot;',"'":'&#39;'}[ch]; }); }
  function pct(v, total){ return (v / total * 100) + '%'; }
  function activeRegion(){
    var g = cur();
    return g ? (g.regions || []).find(function(r){
      return r.id === state.active; }) : null;
  }

  function renderTabs(){
    var box = $('tabs'); box.innerHTML = '';
    DATA.groups.forEach(function(g){
      var b = document.createElement('button');
      b.className = 'tab' + (g.key === state.group ? ' on' : '');
      b.textContent = (g.short || g.key) + ' ' + ((g.regions || []).length);
      b.onclick = function(){
        state.group = g.key; state.active = null; renderAll();
      };
      box.appendChild(b);
    });
  }

  function renderCats(){
    var box = $('cats'); box.innerHTML = '';
    CAT_KEYS.forEach(function(c){
      var b = document.createElement('button');
      b.className = 'cat-chip' + (state.cats.has(c) ? ' on' : '');
      b.innerHTML = '<span class="dot" style="background:' + colorOf(c) +
                    '"></span>' + esc(labelOf(c));
      b.onclick = function(){
        if (state.cats.has(c)) state.cats.delete(c); else state.cats.add(c);
        renderCats(); applyFilters();
      };
      box.appendChild(b);
    });
  }

  function renderStage(){
    var g = cur(); layer.innerHTML = '';
    if (!g) return;
    var isSolid = g.solid !== false;
    $('shot').style.filter = g.dim
      ? 'brightness(' + (1 - g.dim * 0.55).toFixed(2) + ')' : 'none';
    (g.regions || []).forEach(function(r){
      var d = document.createElement('div');
      d.className = 'region';
      d.dataset.id = r.id;
      var c = colorOf(r.cat);
      d.style.left = pct(r.rect[0], DATA.width);
      d.style.top = pct(r.rect[1], DATA.height);
      d.style.width = pct(r.rect[2] - r.rect[0], DATA.width);
      d.style.height = pct(r.rect[3] - r.rect[1], DATA.height);
      d.style.borderColor = c;
      d.style.borderStyle = isSolid ? 'solid' : 'dashed';
      d.style.background = hexA(c, isSolid ? 0.14 : 0.06);
      d.style.setProperty('--hi', hexA(c, 0.42));
      d.style.setProperty('--glow', hexA(c, 0.85));
      if (r.rect[1] < 26) d.classList.add('numin');
      d.innerHTML = '<span class="num" style="color:' + c +
                    ';border-color:' + c + '">' + r.num + '</span>';
      d.addEventListener('click', function(ev){
        ev.stopPropagation();
        select(state.active === r.id ? null : r.id, true);
      });
      d.addEventListener('mouseenter', function(ev){
        hover(r.id, true); showTip(ev, r);
      });
      d.addEventListener('mousemove', moveTip);
      d.addEventListener('mouseleave', function(){
        hover(r.id, false); hideTip();
      });
      layer.appendChild(d);
    });
    (g.on_image_notes || []).forEach(function(n){
      var d = document.createElement('div');
      d.className = 'onnote';
      d.style.left = pct(n.xy[0], DATA.width);
      d.style.top = pct(n.xy[1], DATA.height);
      d.innerHTML = n.lines.map(esc).join('<br>');
      layer.appendChild(d);
    });
    applyNotesToggle();
  }

  function renderList(){
    listEl.innerHTML = '';
    var g = cur(); if (!g) return;
    CAT_KEYS.forEach(function(c){
      var items = (g.regions || []).filter(function(r){
        return r.cat === c; });
      if (!items.length) return;
      var h = document.createElement('div');
      h.className = 'cat-head'; h.dataset.cat = c;
      h.innerHTML = '<span class="dot" style="background:' + colorOf(c) +
                    '"></span>' + esc(labelOf(c));
      listEl.appendChild(h);
      items.forEach(function(r){
        var it = document.createElement('div');
        it.className = 'item'; it.dataset.id = r.id;
        it.innerHTML = '<span class="dot" style="background:' +
          colorOf(c) + '"></span><span class="txt"><span class="nm">' +
          r.num + '. ' + esc(r.name) + '</span><span class="meta">(' +
          r.rect.join(',') + ') · ' + esc(r.purpose) + '</span></span>';
        it.addEventListener('click', function(){
          select(state.active === r.id ? null : r.id, false);
        });
        it.addEventListener('mouseenter', function(){ hover(r.id, true); });
        it.addEventListener('mouseleave', function(){ hover(r.id, false); });
        listEl.appendChild(it);
      });
    });
  }

  function renderInfo(){
    var r = activeRegion(), g = cur();
    if (!r || !g){
      infoEl.textContent = '点击图中的框或右侧条目查看详情';
      return;
    }
    infoEl.innerHTML =
      '<div class="info-title"><span class="dot" style="background:' +
      colorOf(r.cat) + '"></span>' + r.num + '. ' + esc(r.name) + '</div>' +
      '<div class="info-meta">' + esc(labelOf(r.cat)) + ' · ' +
      esc(g.short || g.key) + '</div>' +
      '<div class="kv">坐标 <b>(' + r.rect.join(', ') + ')</b></div>' +
      '<div class="kv">用途 <b>' + esc(r.purpose) + '</b></div>';
  }

  function renderNotes(){
    var g = cur(), html = '';
    if (DATA.scale_note){
      html += '<div class="note warn">⚠ ' + esc(DATA.scale_note) + '</div>';
    }
    if (g && (g.fullscreen_notes || []).length){
      html += '<div class="note-h">★ 全屏识别（不画框）</div>';
      g.fullscreen_notes.forEach(function(n){
        html += '<div class="note">' + esc(n) + '</div>';
      });
    }
    if (g && (g.legend_notes || []).length){
      html += '<div class="note-h">说明 / 动态区域（不画框）</div>';
      g.legend_notes.forEach(function(n){
        html += '<div class="note">' + esc(n) + '</div>';
      });
    }
    $('notes').innerHTML = html;
  }

  function visible(r){
    if (!state.cats.has(r.cat)) return false;
    var q = state.q.trim().toLowerCase();
    if (!q) return true;
    return (r.name + ' ' + r.purpose + ' ' + r.rect.join(','))
      .toLowerCase().indexOf(q) >= 0;
  }

  function applyFilters(){
    var g = cur(); if (!g) return;
    var counts = {};
    (g.regions || []).forEach(function(r){
      var on = visible(r);
      if (on) counts[r.cat] = (counts[r.cat] || 0) + 1;
      var reg = layer.querySelector('.region[data-id="' + r.id + '"]');
      var it = listEl.querySelector('.item[data-id="' + r.id + '"]');
      if (reg) reg.classList.toggle('hidden', !on);
      if (it) it.classList.toggle('hidden', !on);
    });
    listEl.querySelectorAll('.cat-head').forEach(function(h){
      var show = (counts[h.dataset.cat] || 0) > 0;
      h.classList.toggle('hidden', !show);
    });
  }

  function select(id, scroll){
    state.active = id;
    layer.classList.toggle('has-active', !!id);
    document.querySelectorAll('.region').forEach(function(d){
      d.classList.toggle('active', d.dataset.id === id);
    });
    document.querySelectorAll('.item').forEach(function(d){
      d.classList.toggle('active', d.dataset.id === id);
    });
    renderInfo();
    if (scroll && id){
      var it = listEl.querySelector('.item[data-id="' + id + '"]');
      if (it) it.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }

  function hover(id, on){
    var reg = layer.querySelector('.region[data-id="' + id + '"]');
    var it = listEl.querySelector('.item[data-id="' + id + '"]');
    if (reg) reg.classList.toggle('hover', on);
    if (it) it.classList.toggle('hover', on);
  }

  function showTip(ev, r){
    tipEl.innerHTML = '<b>' + r.num + '. ' + esc(r.name) + '</b><br>(' +
      r.rect.join(',') + ') · ' + esc(r.purpose);
    tipEl.style.display = 'block';
    moveTip(ev);
  }
  function moveTip(ev){
    if (tipEl.style.display !== 'block') return;
    var pad = 14, w = tipEl.offsetWidth || 280;
    var x = ev.clientX + pad, y = ev.clientY + pad;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
    if (y + 60 > window.innerHeight - 8) y = ev.clientY - 60;
    tipEl.style.left = Math.max(4, x) + 'px';
    tipEl.style.top = Math.max(4, y) + 'px';
  }
  function hideTip(){ tipEl.style.display = 'none'; }

  function applyNotesToggle(){
    layer.querySelectorAll('.onnote').forEach(function(n){
      n.style.display = state.notes ? '' : 'none';
    });
  }

  function renderAll(){
    renderTabs(); renderCats(); renderStage(); renderList();
    renderInfo(); renderNotes(); applyFilters();
  }

  noteToggle.addEventListener('change', function(){
    state.notes = noteToggle.checked; applyNotesToggle();
  });
  $('search').addEventListener('input', function(ev){
    state.q = ev.target.value; applyFilters();
  });
  $('catAll').onclick = function(){
    state.cats = new Set(CAT_KEYS); renderCats(); applyFilters();
  };
  $('catNone').onclick = function(){
    state.cats = new Set(); renderCats(); applyFilters();
  };
  $('clearSel').onclick = function(){ select(null, false); };
  stage.addEventListener('click', function(ev){
    if (ev.target === stage || ev.target.tagName === 'IMG') select(null, false);
  });
  document.addEventListener('keydown', function(ev){
    if (ev.key === 'Escape') select(null, false);
  });

  var total = DATA.groups.reduce(function(n, g){
    return n + (g.regions || []).length; }, 0);
  $('sub').textContent = DATA.image_name + ' · ' + DATA.width + '×' +
    DATA.height + ' · ' + total + ' 个固定区域 · 点击框/条目联动';
  renderAll();
})();
</script>
</body>
</html>
"""


def render_html(groups, src, manifest, out_dir, scale_note=None,
                image_name=""):
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    img_uri = ("data:image/png;base64,"
               + base64.b64encode(buf.getvalue()).decode("ascii"))
    data = {
        "width": src.size[0], "height": src.size[1],
        "updated": manifest.get("updated"),
        "categories": manifest["categories"],
        "groups": groups,
        "scale_note": scale_note,
        "image_name": image_name,
    }
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (HTML_TEMPLATE
            .replace("__W__", str(src.size[0]))
            .replace("__H__", str(src.size[1]))
            .replace("__IMG_URI__", img_uri)
            .replace("__DATA_JSON__", data_json))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "00_识别区域交互.html"
    path.write_text(html, encoding="utf-8")
    return path


# ==================== main ====================

def main(argv=None):
    ap = argparse.ArgumentParser(description="在游戏截图上标注项目识别区域")
    ap.add_argument("--image", help="用户提供的游戏截图路径")
    ap.add_argument("--out", default=str(REPO / "logs" / "region_maps"),
                    help="输出目录（默认 logs/region_maps，gitignored）")
    ap.add_argument("--groups", default="",
                    help="只渲染指定组（逗号分隔：main,windows,minigames），默认全部")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--format", default="both",
                    choices=("both", "png", "html"),
                    help="输出格式：both（默认）/ png / html")
    ap.add_argument("--no-scale", action="store_true",
                    help="截图尺寸与基准不符时不缩放（仅在自担风险时用）")
    ap.add_argument("--list", action="store_true", help="列出组与区域数量")
    args = ap.parse_args(argv)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    groups = manifest.get("groups", [])
    if args.list:
        for g in groups:
            print(f"{g['key']}\t{len(g.get('regions', []))} 区域\t{g.get('title')}")
        return 0
    if not args.image:
        ap.error("必须提供 --image <截图路径>（请用户提供一张游戏截屏）")

    img_path = Path(args.image)
    if not img_path.exists():
        print(json.dumps({"error": f"截图不存在: {img_path}"},
                         ensure_ascii=False))
        return 2
    src = Image.open(img_path).convert("RGB")
    W, H = src.size
    bw, bh = manifest.get("base_size", [1378, 831])
    warnings = []
    scale_note = None
    sx, sy = 1.0, 1.0
    if (W, H) != (bw, bh):
        if args.no_scale:
            warnings.append(f"截图 {W}x{H} != 基准 {bw}x{bh}，按原坐标绘制")
            scale_note = f"截图尺寸 {W}x{H} 与基准 {bw}x{bh} 不符，按原坐标（可能错位）"
        else:
            sx, sy = W / float(bw), H / float(bh)
            warnings.append(f"截图 {W}x{H} != 基准 {bw}x{bh}，已按比例缩放")
            scale_note = (f"截图尺寸 {W}x{H} 与基准 {bw}x{bh} 不符，"
                          f"标注已按比例缩放（x×{sx:.3f}, y×{sy:.3f}）")

    wanted = [s.strip() for s in args.groups.split(",") if s.strip()]
    prepared = []
    for group in groups:
        if wanted and group["key"] not in wanted:
            continue
        g = dict(group)
        if sx != 1.0 or sy != 1.0:
            g["regions"] = [scale_region(r, sx, sy)
                            for r in group.get("regions", [])]
            g["on_image_notes"] = [
                {"xy": [int(n["xy"][0] * sx), int(n["xy"][1] * sy)],
                 "lines": n["lines"]}
                for n in group.get("on_image_notes", [])]
        prepared.append(g)

    out_dir = Path(args.out)
    outputs = []
    if args.format in ("both", "png"):
        for g in prepared:
            path = render_group(g, src, manifest, out_dir, warnings,
                                scale_note)
            outputs.append({"group": g["key"], "kind": "png",
                            "path": str(path),
                            "regions": len(g.get("regions", []))})
    if args.format in ("both", "html"):
        path = render_html(prepared, src, manifest, out_dir, scale_note,
                           img_path.name)
        outputs.append({"group": "all", "kind": "html",
                        "path": str(path),
                        "regions": sum(len(g.get("regions", []))
                                       for g in prepared)})

    print(json.dumps({"image": str(img_path), "size": [W, H],
                      "outputs": outputs, "warnings": warnings},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
