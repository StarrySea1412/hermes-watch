"""Health report generation: structured data -> stored HTML. Local-only, no cloud.

报告呈现为一份正式巡检文档：灰底居中的「纸面」版式、衬线标题、封面元信息表、
结论印章、主机总览表、磁盘趋势 SVG 走线、明细指标条、处置建议、页脚。
自适应查看者系统明暗偏好（prefers-color-scheme），支持打印/导出 PDF。
"""
import html
import statistics
import time

import httpx

from . import db, rules

SEV_COLOR = {"crit": "#ef4444", "warn": "#f59e0b", "info": "#3b82f6"}
SEV_LABEL = {"crit": "严重", "warn": "警告", "info": "提示"}

# 按发现类型给出处置建议（规则引擎的确定性输出，非 AI）
RECO = {
    "disk": "清理大文件与历史日志（如 journalctl --vacuum-size=200M），并为高频写入目录配置 logrotate 轮转。",
    "memory": "用 ps aux --sort=-rss 定位高内存进程；疑似泄漏的进程安排低峰期重启，并排查其缓存/句柄逻辑。",
    "login": "核实登录来源是否授权；必要时封禁来源 IP、轮换账号凭据，并建议启用 fail2ban 类防护。",
    "service": "systemctl status <服务名> 查看失败原因，修复后执行 systemctl reset-failed 清理状态。",
    "cpu": "用 top 定位热点进程，评估是否优化任务调度或扩容。",
}

VERDICTS = ((90, "#22c55e", "运行平稳"), (70, "#f59e0b", "需要关注"), (0, "#ef4444", "需要处置"))


def _fmt_window(seconds: float) -> str:
    if seconds <= 0:
        return "暂无采集数据"
    m = seconds / 60
    if m < 180:
        return f"最近 {m:.0f} 分钟"
    h = m / 60
    if h < 48:
        return f"最近 {h:.1f} 小时"
    return f"最近 {h / 24:.1f} 天"


def _score_cls(score: int) -> str:
    return "s2" if score >= 90 else ("s1" if score >= 70 else "s0")


def _verdict(score: int):
    for floor, color, label in VERDICTS:
        if score >= floor:
            return color, label
    return VERDICTS[-1][1], VERDICTS[-1][2]


def _chip(sev: str) -> str:
    c = SEV_COLOR.get(sev, "#888")
    return (f'<span class="chip" style="color:{c};border-color:{c}55;'
            f'background:{c}18">{SEV_LABEL.get(sev, sev)}</span>')


def _spark(vals: list, color: str, w: int = 170, h: int = 34) -> str:
    """磁盘历史走线（内联 SVG，离线可渲染）：渐变面积 + 折线 + 末端圆点。"""
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    px = [i / (len(vals) - 1) * w for i in range(len(vals))]
    py = [h - 4 - (v - lo) / rng * (h - 10) for v in vals]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(px, py))
    gid = f"g{abs(hash(tuple(vals))) % 99999}"
    lx, ly = px[-1], py[-1]
    return (f'<svg class="spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{color}" stop-opacity="0.28"/>'
            f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="0,{h} {pts} {w},{h}" fill="url(#{gid})" stroke="none"/>'
            f'<polyline fill="none" stroke="{color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round" points="{pts}"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.6" fill="{color}"/>'
            f'<text x="{w}" y="9" text-anchor="end" font-size="9" fill="{color}">{hi:.0f}%</text></svg>')


def _pct(v) -> str:
    return f"{v:.0f}%" if v is not None else "—"


def generate(kind: str = "manual") -> dict:
    hosts = db.query("SELECT * FROM hosts ORDER BY id")
    gen_ts = db.now()
    gen_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(gen_ts))
    win = db.query_one("SELECT COUNT(*) AS n, MIN(ts) AS lo, MAX(ts) AS hi FROM metrics")
    window = _fmt_window((win["hi"] or 0) - (win["lo"] or 0)) if win else "暂无采集数据"

    sections, overview_rows, recos, top_findings, scores = [], [], [], [], []
    n_ok = n_warn = n_crit = 0
    for h in hosts:
        latest = db.query_one(
            "SELECT * FROM metrics WHERE host_id=? ORDER BY ts DESC LIMIT 1", (h["id"],))
        fs = db.query(
            "SELECT * FROM findings WHERE host_id=? AND status IN ('open','analyzed')", (h["id"],))
        score = rules.health_score(fs)
        scores.append(score)
        if score >= 90:
            n_ok += 1
        elif score >= 70:
            n_warn += 1
        else:
            n_crit += 1
        enriched = [{**f, "host": h["name"]} for f in fs]
        top_findings.extend(enriched)
        for f in enriched:
            if f["type"] in RECO:
                recos.append((f["host"], f["title"], RECO[f["type"]]))

        l = latest or {}
        st_color, st_label = _verdict(score)
        ov_color = "#22c55e" if score >= 90 else ("#f59e0b" if score >= 70 else "#ef4444")
        overview_rows.append(
            f"<tr><td><b>{html.escape(h['name'])}</b></td>"
            f"<td>{html.escape(h['group_name'] or 'default')}</td>"
            f"<td><b class='{_score_cls(score)}'>{score}</b></td>"
            f"<td style='color:{st_color}'>{st_label}</td>"
            f"<td>{_pct(l.get('cpu') if l else None)}</td>"
            f"<td>{_pct(l.get('mem') if l else None)}</td>"
            f"<td>{_pct(l.get('disk') if l else None)}</td>"
            f"<td>{len(fs) or '—'}</td></tr>")

        def bar(label, v, color):
            if v is None:
                return ""
            warn = v >= 85
            return (f'<div class="metric"><span>{label}</span>'
                    f'<div class="bar"><i style="width:{min(100, v):.0f}%;background:{color}"></i></div>'
                    f'<b class="{"hot" if warn else ""}">{v:.0f}%</b></div>')

        metrics = "".join([
            bar("CPU", l.get("cpu"), "#0ea5e9"),
            bar("内存", l.get("mem"), "#8b5cf6"),
            bar("磁盘", l.get("disk"), "#f59e0b"),
            (f'<div class="metric"><span>负载</span><div class="bar"><i style="width:'
             f'{min(100, (l.get("load1") or 0) / 8 * 100):.0f}%;background:#10b981"></i></div>'
             f'<b>{(l.get("load1") or 0):.2f}</b></div>') if l else "",
        ])

        rows = db.query("SELECT disk FROM metrics WHERE host_id=? ORDER BY ts", (h["id"],))
        spark = _spark([r["disk"] for r in rows[-80:]], "#f59e0b")
        trend = ""
        if len(rows) > 60:
            first = statistics.mean(r["disk"] for r in rows[:30])
            last = statistics.mean(r["disk"] for r in rows[-30:])
            arrow = "↗ 增长" if last - first > 2 else ("↘ 回落" if first - last > 2 else "→ 平稳")
            trend = f'<span class="trend">磁盘趋势 <b>{last:.0f}%</b>（{arrow}）</span>'

        items = "".join(
            f'<li>{_chip(f["severity"])}<div><b>{html.escape(f["title"])}</b>'
            f'<p>{html.escape(f["detail"])}</p>'
            + (f'<p class="reco">建议：{RECO[f["type"]]}</p>' if f["type"] in RECO else "")
            + "</div></li>"
            for f in sorted(fs, key=lambda x: rules.severity_rank(x["severity"])))
        sections.append(f"""
        <div class="card">
          <div class="head">
            <span class="score {_score_cls(score)}">{score}</span>
            <div class="ht"><h3>{html.escape(h['name'])} <small>{html.escape(h['hostname'])} · {html.escape(h['group_name'] or 'default')}</small></h3>
            <p><span class="chip" style="color:{st_color};border-color:{st_color}55;background:{st_color}18">{st_label}</span>
            {trend}</p></div>
            <div class="side"><div class="side-label">磁盘走势</div>{spark}</div>
          </div>
          {f'<div class="metrics">{metrics}</div>' if metrics else ''}
          {'<ul class="finds">' + items + '</ul>' if items else '<p class="allok">✓ 未发现异常</p>'}
        </div>""")

    top_findings.sort(key=lambda f: rules.severity_rank(f["severity"]))
    top_html = "".join(
        f'<li>{_chip(f["severity"])}<div><b>{html.escape(f["host"])}</b> — '
        f'{html.escape(f["title"])}<p>{html.escape(f["detail"])}</p></div></li>'
        for f in top_findings[:10])
    reco_items, seen = [], set()
    for host, title, reco in recos:
        if reco in seen:
            continue
        seen.add(reco)
        reco_items.append(
            f'<li><b>{html.escape(host)}</b> · {html.escape(title)}<p>{reco}</p></li>')
    reco_html = "".join(reco_items)

    overall = round(statistics.mean(scores)) if scores else 100
    v_color, v_label = _verdict(overall)
    data = {"overall": overall, "hosts": len(hosts),
            "findings": len(top_findings), "kind": kind}
    kind_label = "定时巡检" if kind == "auto" else "手动生成"
    overview = "".join(overview_rows)
    content = f"""<!doctype html><html><head><meta charset="utf-8">
    <style>
      /* 报告自适应日/夜：跟随查看者系统偏好（iframe/新窗口内独立于面板主题） */
      :root {{ --bg:#e6eaf1; --fg:#2a3a55; --paper:#ffffff; --bd:#dce3ee; --mute:#7c8aa0;
              --score-bg:#eef3fa; --hot:#dc2626; --shadow:0 14px 44px rgba(24,42,74,.16); }}
      @media (prefers-color-scheme: dark) {{
        :root {{ --bg:#070c16; --fg:#dbe4f0; --paper:#101a2c; --bd:#223049; --mute:#7c8aa0;
                --score-bg:#1c2a44; --hot:#f87171; --shadow:0 14px 44px rgba(0,0,0,.5); }}
      }}
      *{{box-sizing:border-box}}
      body{{background:var(--bg);color:var(--fg);margin:0;line-height:1.7;
           font-family:system-ui,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif}}
      /* 纸面：文档感的关键 —— 灰底居中一张「纸」 */
      .paper{{max-width:860px;margin:30px auto 60px;background:var(--paper);
             border:1px solid var(--bd);border-radius:4px;box-shadow:var(--shadow);padding:52px 58px 44px}}
      .serif{{font-family:Georgia,'Times New Roman','STZhongsong','SimSun',serif}}
      /* 封面 */
      .cover{{position:relative;border-bottom:1px solid var(--bd);padding-bottom:26px;margin-bottom:8px}}
      .brand{{color:var(--mute);font-size:11.5px;letter-spacing:.28em}}
      h1{{font-size:34px;margin:18px 0 22px;letter-spacing:.06em;line-height:1.3}}
      table.meta{{border-collapse:collapse;font-size:12.5px;min-width:70%}}
      table.meta td{{padding:3px 26px 3px 0;color:var(--mute);white-space:nowrap}}
      table.meta td.k{{color:var(--mute);width:88px;letter-spacing:.06em}}
      table.meta td.v{{color:var(--fg);font-weight:600}}
      .stamp{{position:absolute;top:6px;right:0;transform:rotate(7deg);
             border:3px double {v_color};border-radius:8px;color:{v_color};
             padding:7px 20px;font-size:19px;font-weight:800;letter-spacing:.34em;
             opacity:.82;font-family:inherit}}
      .stamp small{{display:block;font-size:10px;letter-spacing:.12em;font-weight:600;text-align:center;margin-top:2px;opacity:.8}}
      /* 章节标题：衬线 + 双线装饰 */
      h2{{font-size:18px;margin:34px 0 14px;letter-spacing:.05em;
         padding-bottom:8px;border-bottom:1px solid var(--bd);position:relative}}
      h2::after{{content:'';position:absolute;left:0;bottom:-1px;width:64px;height:2px;background:#3b82f6}}
      h2 .no{{color:var(--mute);font-size:12px;margin-right:10px;letter-spacing:.2em}}
      /* 摘要 */
      .summary{{display:flex;gap:22px;align-items:center}}
      .score{{font-size:30px;font-weight:700;width:66px;height:66px;border-radius:50%;flex-shrink:0;
        display:flex;align-items:center;justify-content:center;background:var(--score-bg);
        border:1px solid var(--bd)}}
      .s2{{color:#16a34a}} .s1{{color:#d97706}} .s0{{color:#dc2626}}
      .summary p{{margin:2px 0}}
      .kpis{{display:flex;gap:20px;flex-wrap:wrap;margin-top:8px;color:var(--mute);font-size:12.5px}}
      .kpis b{{color:var(--fg);font-size:16px;margin-right:4px}}
      /* 总览表 */
      table.grid{{width:100%;border-collapse:collapse;font-size:12.5px}}
      table.grid th{{text-align:left;color:var(--mute);font-weight:600;font-size:11px;
        letter-spacing:.08em;padding:8px 10px;border-bottom:1.5px solid var(--bd)}}
      table.grid td{{padding:8px 10px;border-bottom:1px dashed var(--bd)}}
      table.grid tr:last-child td{{border-bottom:none}}
      /* 卡片 */
      .card{{background:var(--paper);border:1px solid var(--bd);border-radius:10px;padding:16px 20px;margin:14px 0}}
      .head{{display:flex;gap:16px;align-items:center}}
      .ht{{flex:1;min-width:0}} .ht h3{{margin:0;font-size:15.5px}} .ht small{{color:var(--mute);font-weight:400;font-size:11.5px}}
      .ht p{{margin:4px 0 0;font-size:12px}} .trend{{color:var(--mute)}}
      .side{{text-align:right;flex-shrink:0}} .side-label{{color:var(--mute);font-size:10px;letter-spacing:.15em;margin-bottom:2px}}
      .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:6px 24px;
        margin-top:12px;padding-top:12px;border-top:1px dashed var(--bd)}}
      .metric{{display:flex;align-items:center;gap:9px;font-size:12px;color:var(--mute)}}
      .metric span{{width:30px;flex-shrink:0}}
      .metric .bar{{flex:1;height:5px;border-radius:3px;background:var(--bd);overflow:hidden}}
      .metric b{{width:44px;text-align:right;color:var(--fg);font-weight:600}}
      .metric b.hot{{color:var(--hot)}}
      /* 发现列表 */
      ul,ol{{list-style:none;margin:12px 0 0;padding:0}}
      ol.prio{{counter-reset:prio}}
      ol.prio>li{{counter-increment:prio;display:flex;gap:12px;padding:10px 0;border-top:1px dashed var(--bd);font-size:13px}}
      ol.prio>li::before{{content:counter(prio,decimal-leading-zero);flex-shrink:0;color:var(--mute);
        font-size:12px;font-weight:700;letter-spacing:.05em;margin-top:2px}}
      .finds>li{{display:flex;gap:10px;padding:10px 0;border-top:1px dashed var(--bd);font-size:13px}}
      .finds>li p{{margin:3px 0 0;color:var(--mute);font-size:12.5px}}
      .reco{{color:#60a5fa !important}}
      .chip{{flex-shrink:0;font-size:11px;font-weight:700;border:1px solid;border-radius:999px;
        padding:1px 9px;height:fit-content;margin-top:1px}}
      .allok{{color:#16a34a;font-size:13px;margin:10px 0 0}}
      /* 页脚 */
      footer{{margin-top:40px;padding-top:18px;border-top:1px solid var(--bd);
        color:var(--mute);font-size:11.5px;line-height:2.1;display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}}
      .sig{{text-align:right}}
      .sig b{{font-family:Georgia,'SimSun',serif;letter-spacing:.2em;color:var(--fg)}}
      @media print {{
        body{{background:#fff}}
        .paper{{margin:0;box-shadow:none;border:none;max-width:none;padding:24px 8px}}
        .card{{break-inside:avoid}}
      }}
    </style></head><body><div class="paper">
    <div class="cover">
      <div class="brand">🐚 HERMES WATCH · 本地巡检平台</div>
      <h1 class="serif">服务器巡检健康报告</h1>
      <div class="stamp serif">{v_label}<small>HERMES WATCH</small></div>
      <table class="meta">
        <tr><td class="k">报告编号</td><td class="v">HW-{gen_ts:.0f}</td>
            <td class="k">触发方式</td><td class="v">{kind_label}</td></tr>
        <tr><td class="k">生成时间</td><td class="v">{gen_str}</td>
            <td class="k">数据范围</td><td class="v">{window} · {win['n'] if win else 0} 个采集点</td></tr>
      </table>
    </div>

    <h2 class="serif"><span class="no">01</span>执行摘要</h2>
    <div class="card summary">
      <span class="score {_score_cls(overall)}">{overall}</span>
      <div style="flex:1">
        <p>结论：<b style="color:{v_color};font-size:15px">Fleet {v_label}</b> — 建议{"保持现有巡检节奏" if overall >= 90 else "按下方优先级依次处置"}</p>
        <div class="kpis">
          <span><b>{len(hosts)}</b>台主机</span>
          <span style="color:#16a34a"><b>{n_ok}</b>健康</span>
          <span style="color:#d97706"><b>{n_warn}</b>关注</span>
          <span style="color:#dc2626"><b>{n_crit}</b>处置</span>
          <span><b>{len(top_findings)}</b>条待处理发现</span>
        </div>
      </div>
    </div>

    <h2 class="serif"><span class="no">02</span>主机总览</h2>
    <div class="card" style="padding:6px 14px">
      <table class="grid">
        <thead><tr><th>主机</th><th>分组</th><th>健康分</th><th>状态</th><th>CPU</th><th>内存</th><th>磁盘</th><th>待处理发现</th></tr></thead>
        <tbody>{overview or '<tr><td colspan="8" style="color:var(--mute)">暂无主机，请先在设置页接入</td></tr>'}</tbody>
      </table>
    </div>

    <h2 class="serif"><span class="no">03</span>优先处理 Top {min(10, len(top_findings)) if top_findings else 0}</h2>
    <ol class="prio">{top_html or '<li class="allok">✓ 全部正常，无需处理</li>'}</ol>

    <h2 class="serif"><span class="no">04</span>主机明细</h2>
    {''.join(sections)}

    <h2 class="serif"><span class="no">05</span>处置建议汇总</h2>
    <div class="card"><ul>{reco_html or '<li class="allok">✓ 暂无需要建议处置的事项</li>'}</ul></div>

    <!--AI_SECTION-->
    <footer>
      <div>本报告由 Hermes Watch 本地规则引擎基于巡检采集数据自动生成，<br>
      结论可回跳诊断中心查看证据链，全程数据不出本机。<br>
      健康分 = 规则引擎对未处理发现的加权扣分（阈值见设置页）</div>
      <div class="sig">HERMES WATCH<br><b>运维巡检 · 本地出具</b><br>{gen_str}</div>
    </footer>
    </div></body></html>"""
    rid = db.execute(
        "INSERT INTO reports(ts,kind,title,score,data,content_html) VALUES(?,?,?,?,?,?)",
        (gen_ts, kind, "Fleet 健康报告", overall, db.j(data), content))
    db.execute("INSERT INTO events(ts,host_id,kind,message,data) VALUES(?,NULL,'report',?,?)",
               (gen_ts, f"生成健康报告（{kind_label}），整体健康分 {overall}", db.j({"report_id": rid})))
    return {"id": rid, **data}


def _ai_context() -> str:
    """给报告 AI 摘要的数据底稿（与报告同源的规则引擎事实）。"""
    hosts = db.query("SELECT * FROM hosts ORDER BY id")
    lines = []
    for h in hosts:
        fs = db.query(
            "SELECT severity, title FROM findings WHERE host_id=? AND status IN ('open','analyzed')",
            (h["id"],))
        m = db.query_one(
            "SELECT cpu, mem, disk FROM metrics WHERE host_id=? ORDER BY ts DESC LIMIT 1", (h["id"],))
        score = rules.health_score(fs)
        lines.append(f"- {h['name']}: 健康分 {score}, CPU {m['cpu'] if m else 0:.0f}%, "
                     f"内存 {m['mem'] if m else 0:.0f}%, 磁盘 {m['disk'] if m else 0:.0f}%, "
                     + (f"未处理发现: {'; '.join(f['title'] for f in fs)}" if fs else "无未处理发现"))
    return "\n".join(lines)


async def attach_ai_summary(rid: int) -> dict:
    """给已生成的报告补 AI 摘要（若 AI 外发开启且端点可用）。
    AI 只写叙事、绝不改分数与结论；失败在报告里留痕，不阻塞报告本身。"""
    row = db.query_one("SELECT * FROM reports WHERE id=?", (rid,))
    if not row or "<!--AI_SECTION-->" not in (row["content_html"] or ""):
        return {"ai": False}
    data = db.uj(row["data"], {}) or {}

    from . import analysis
    conf = dict(analysis._ai_conf())
    if not (analysis._llm_enabled() and conf.get("base_url")):
        return {"ai": False}  # 未开启：报告保持纯规则引擎版本

    conf["base_url"] = await analysis.resolve_base(conf)
    prompt = (f"以下是服务器巡检报告的数据底稿（来自确定性规则引擎，整体健康分 {data.get('overall')}）：\n"
              f"{_ai_context()}\n\n"
              "请用不超过 6 句中文写一段报告摘要：1) 整体健康判断；2) 最需要关注的主机与风险；"
              "3) 建议的处置顺序。只基于以上数据，不要编造，不要给分数以外的评价。")
    try:
        async with httpx.AsyncClient(timeout=40) as cli:
            headers = {"Authorization": f"Bearer {conf['api_key']}"} if conf.get("api_key") else {}
            r = await cli.post(conf["base_url"].rstrip("/") + "/chat/completions",
                               headers=headers,
                               json={"model": conf.get("model", "gpt-4o-mini"),
                                     "messages": [{"role": "user", "content": prompt}],
                                     "max_tokens": 400})
            text = (r.json()["choices"][0]["message"]["content"] or "").strip()
            model = conf.get("model", "")
    except Exception as e:
        text, model = "", ""
        err = f"{type(e).__name__}: {e}"[:160]
    else:
        err = ""

    if text:
        ai_html = (f'<h2 class="serif"><span class="no">06</span>AI 摘要</h2>'
                   f'<div class="card"><p style="margin:2px 0;line-height:1.9">{html.escape(text)}</p>'
                   f'<p style="margin:10px 0 0;color:var(--mute);font-size:11px">'
                   f'AI 叙事 · 由 {html.escape(model or "LLM")} 生成，仅供参考，一切以规则引擎结论与证据链为准</p></div>')
    else:
        ai_html = (f'<h2 class="serif"><span class="no">06</span>AI 摘要</h2>'
                   f'<div class="card"><p style="margin:0;color:var(--mute)">AI 摘要生成失败（{html.escape(err)}）'
                   f'——本报告的规则引擎结论不受影响。</p></div>')
    data.update({"ai_summary": text or None, "ai_model": model or None, "ai_error": err or None})
    db.execute("UPDATE reports SET data=?, content_html=? WHERE id=?",
               (db.j(data), row["content_html"].replace("<!--AI_SECTION-->", ai_html), rid))
    return {"ai": bool(text), "error": err or None}


def list_reports(limit=20):
    rows = db.query("SELECT id,ts,kind,title,score,data FROM reports ORDER BY ts DESC LIMIT ?", (limit,))
    for r in rows:
        r["data"] = db.uj(r["data"], {})
    return rows


def render_status_page(snap: dict) -> str:
    """公开状态页（带 token 只读分享）：健康概览 + 主机状态 + 未处理发现标题。
    刻意不包含：主机 IP/地址、SSH 凭据、证据链输出、终端入口。自动刷新 60s。"""
    hosts = snap["hosts"]
    scores = [h["score"] for h in hosts] or [100]
    overall = round(sum(scores) / len(scores))
    color, verdict = _verdict(overall)
    gen_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snap.get("generated_at") or time.time()))

    STATUS_LABEL = {"ok": ("运行正常", "#22c55e"), "warn": ("需要关注", "#f59e0b"),
                    "crit": ("需要处置", "#ef4444"), "offline": ("离线", "#94a3b8")}
    cards = []
    for h in hosts:
        l = h.get("latest") or {}
        label, c = STATUS_LABEL.get(h["status"], STATUS_LABEL["ok"])
        def _bar(v, name):
            if v is None:
                return ""
            hot = v >= 85
            return (f'<div class="metric"><span>{name}</span>'
                    f'<div class="bar"><i style="width:{min(100, v):.0f}%;background:var(--m-{name.lower()})"></i></div>'
                    f'<b class="{"hot" if hot else ""}">{v:.0f}%</b></div>')
        cards.append(f"""
      <div class="host">
        <div class="hrow"><b>{html.escape(h['name'])}</b>
          <span class="chip" style="color:{c};border-color:{c}55;background:{c}18">{label}</span>
          <span class="score" style="color:{c}">{h['score']}</span></div>
        {_bar(l.get('cpu'), 'CPU')}{_bar(l.get('mem'), '内存')}{_bar(l.get('disk'), '磁盘')}
      </div>""")

    fs = db.query(
        "SELECT f.severity, f.title, h.name AS host_name FROM findings f "
        "JOIN hosts h ON h.id=f.host_id WHERE f.status IN ('open','analyzed') "
        "ORDER BY CASE f.severity WHEN 'crit' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, f.ts DESC LIMIT 12")
    finding_rows = "".join(
        f'<li><span class="dot" style="background:{SEV_COLOR.get(f["severity"], "#888")}"></span>'
        f'{html.escape(f["title"])}<em>{html.escape(f["host_name"])}</em></li>'
        for f in fs) or '<li class="none">当前没有待处理的发现 —— 一切正常</li>'

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Hermes Watch · 服务状态</title>
<style>
  :root {{ color-scheme: light dark; --bg:#f4f5f7; --panel:#ffffff; --text:#1a2333; --mute:#64748b;
          --line:#e2e8f0; --m-cpu:#0ea5e9; --m-内存:#8b5cf6; --m-cpu2:#0ea5e9; --m-磁盘:#f59e0b; --m-内存2:#8b5cf6; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0a0f1c; --panel:#101828; --text:#e6edf7; --mute:#8fa3bd; --line:#223049;
            --m-cpu:#38bdf8; --m-磁盘:#fbbf24; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:32px 16px; background:var(--bg); color:var(--text);
         font:14px/1.6 "PingFang SC","Microsoft YaHei",system-ui,sans-serif; }}
  .page {{ max-width:760px; margin:0 auto; }}
  .head {{ display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }}
  h1 {{ font-size:20px; margin:0; letter-spacing:.5px; }}
  .verdict {{ font-size:26px; font-weight:700; color:{color}; }}
  .meta {{ color:var(--mute); font-size:12px; margin:6px 0 22px; }}
  .host {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin-bottom:10px; }}
  .hrow {{ display:flex; align-items:center; gap:10px; margin-bottom:8px; }}
  .hrow b {{ font-size:15px; }}
  .chip {{ margin-left:auto; font-size:11px; padding:2px 9px; border-radius:99px; border:1px solid; }}
  .score {{ font-size:18px; font-weight:700; font-variant-numeric:tabular-nums; }}
  .metric {{ display:flex; align-items:center; gap:10px; margin-top:5px; font-size:12px; color:var(--mute); }}
  .metric span {{ width:34px; }}
  .bar {{ flex:1; height:5px; border-radius:3px; background:var(--line); overflow:hidden; }}
  .bar i {{ display:block; height:100%; border-radius:3px; }}
  .metric b {{ width:38px; text-align:right; color:var(--text); font-variant-numeric:tabular-nums; }}
  .metric b.hot {{ color:#ef4444; }}
  h2 {{ font-size:14px; color:var(--mute); font-weight:600; margin:24px 0 10px; }}
  ul {{ list-style:none; margin:0; padding:0; background:var(--panel); border:1px solid var(--line);
       border-radius:12px; overflow:hidden; }}
  li {{ display:flex; align-items:center; gap:10px; padding:9px 14px; font-size:13px;
       border-top:1px solid var(--line); }}
  li:first-child {{ border-top:0; }}
  li.none {{ color:var(--mute); }}
  .dot {{ width:8px; height:8px; border-radius:50%; flex-shrink:0; }}
  li em {{ margin-left:auto; font-style:normal; color:var(--mute); font-size:11.5px; }}
  footer {{ margin-top:26px; color:var(--mute); font-size:11.5px; text-align:center; line-height:1.8; }}
</style></head><body><div class="page">
  <div class="head"><h1>Hermes Watch</h1><span class="verdict">{overall}</span>
    <span style="color:{color};font-weight:600">{verdict}</span></div>
  <div class="meta">Fleet 平均健康分 · {len(hosts)} 台主机 · 每分钟自动刷新 · 生成于 {gen_str}</div>
  {''.join(cards)}
  <h2>待处理发现</h2>
  <ul>{finding_rows}</ul>
  <footer>本页由 Hermes Watch 本地规则引擎生成 · 只读状态页，不含主机地址与巡检证据<br>
  完整诊断与运维能力请访问面板（需授权）</footer>
</div></body></html>"""
