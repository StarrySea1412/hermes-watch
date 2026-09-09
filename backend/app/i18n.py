"""后端数据文案 i18n：发现/事件/提案/诊断卡跟随面板语言（settings.hw_lang，前端切换时同步）。

先例：reports.py 已实现「报告跟随面板语言」（_report_lang + REPORT_I18N），本模块沿用同一模式：
- lang() → 读 settings.hw_lang；键不存在或非法时回退 'zh'
- t(zh, en) → 按当前语言二选一。调用处成对给出双语字面量（不建大字典表），review 友好；
  f-string 先求值，t 只负责挑语言。中文仍是存库事实源，翻译发生在生成出口（广播/通知/卡片）
  与 API 读出口（tr_* 系列正则兜底，让历史中文行也跟随语言）。

关于诊断卡（findings.card）：card 是 JSON 整体存取，读出口逐字段翻译过于复杂，
故在生成处（analysis.py）直接用 t()——存库语言跟随生成时的面板语言。
这与「报告生成时即用当时语言」的先例一致，且 mock 演示数据本就是演示用途。

tr_* 系列是纯函数正则兜底（不用 LLM）：EN 面板下对存量中文做模板匹配翻译，
匹配不到的原样保留；'zh' 面板下原样返回。
"""
import re

from . import db


def lang() -> str:
    s = db.query_one("SELECT value FROM settings WHERE key='hw_lang'")
    return "en" if s and s["value"] == "en" else "zh"


def t(zh: str, en: str) -> str:
    """按面板语言从一对双语字面量中挑一个。"""
    return en if lang() == "en" else zh


# ---------- 读出口兜底：存量中文 → 英文（正则模板匹配，历史行跟随语言） ----------

# 发现标题模板（覆盖 rules.py / ports.py 全部 + 测试注入的【测试】前缀）
_FINDING_TITLE_RULES = [
    (re.compile(r"^磁盘使用率 (\d+(?:\.\d+)?)%$"), lambda m: f"Disk usage {m.group(1)}%"),
    (re.compile(r"^内存使用率 (\d+(?:\.\d+)?)%$"), lambda m: f"Memory usage {m.group(1)}%"),
    (re.compile(r"^CPU 使用率 (\d+(?:\.\d+)?)%$"), lambda m: f"CPU usage {m.group(1)}%"),
    (re.compile(r"^系统负载 (\d+(?:\.\d+)?)$"), lambda m: f"System load {m.group(1)}"),
    (re.compile(r"^磁盘持续高写入 (\d+(?:\.\d+)?) MB/s$"),
     lambda m: f"Sustained high disk writes {m.group(1)} MB/s"),
    (re.compile(r"^温度过高 (\d+)°C$"), lambda m: f"High temperature {m.group(1)}°C"),
    (re.compile(r"^Swap 使用率 (\d+(?:\.\d+)?)%$"), lambda m: f"Swap usage {m.group(1)}%"),
    (re.compile(r"^服务失败: (.+)$"), lambda m: f"Service failed: {m.group(1)}"),
    (re.compile(r"^可疑登录: (.+)$"), lambda m: f"Suspicious login: {m.group(1)}"),
    (re.compile(r"^证书 (\d+) 天后到期$"), lambda m: f"Certificate expires in {m.group(1)} days"),
    (re.compile(r"^新增监听端口 (\d+)$"), lambda m: f"New listening port {m.group(1)}"),
    (re.compile(r"^【测试】(.+)$"), lambda m: f"[TEST] {m.group(1)}"),
]

# 发现详情模板（同上）
_FINDING_DETAIL_RULES = [
    (re.compile(r"^磁盘即将写满，有写入失败风险$"),
     lambda m: "Disk almost full — writes may fail"),
    (re.compile(r"^超过 (\d+)% 警戒线$"),
     lambda m: f"Exceeds {m.group(1)}% warning line"),
    (re.compile(r"^内存逼近 OOM 边界$"),
     lambda m: "Memory approaching OOM boundary"),
    (re.compile(r"^持续高负载$"), lambda m: "Sustained high load"),
    (re.compile(r"^load1 超过 (\d+(?:\.\d+)?)$"), lambda m: f"load1 above {m.group(1)}"),
    (re.compile(r"^写入速率超过 (\d+) MB/s 警戒线（agent 上报）$"),
     lambda m: f"Write rate Exceeds {m.group(1)} MB/s warning line (agent report)"),
    (re.compile(r"^传感器温度超过 (\d+)°C$"),
     lambda m: f"Sensor temperature Exceeds {m.group(1)}°C"),
    (re.compile(r"^swap 持续高位（>(\d+)%）通常意味着物理内存不足$"),
     lambda m: f"swap persistently high (>{m.group(1)}%) usually means physical memory pressure"),
    (re.compile(r"^systemd 检测到 failed unit$"), lambda m: "systemd detected a failed unit"),
    (re.compile(r"^非常见来源的 root/sudo 登录$"),
     lambda m: "root/sudo login from an uncommon source"),
    (re.compile(r"^TLS 证书即将过期$"), lambda m: "TLS certificate expiring soon"),
    (re.compile(r"^端口 (\d+) 不在既有基线中(?:，监听进程 (.+))?$"),
     lambda m: (f"Port {m.group(1)} is not in the existing baseline"
                + (f", listening process: {m.group(2)}" if m.group(2) else ""))),
]

# 事件模板（覆盖 scheduler / executor / analysis / reports 落库的全部 message）
# 报告 kind 历史中文词 → EN（Manual/Scheduled 词表来自 scheduler 写入口径）
_REPORT_KIND_EN = {"手动生成": "Manual", "定时": "Scheduled"}


_EVENT_RULES = [
    (re.compile(r"^采集失败: (.+)$"), lambda m: f"Collection failed: {m.group(1)}"),
    (re.compile(r"^采集恢复，巡检数据恢复更新$"),
     lambda m: "Collection recovered — inspection data resumed updating"),
    (re.compile(r"^发现已自动恢复: (.+)（持续 (.+?)，连续 (\d+) 轮未再触发）$"),
     lambda m: (f"Finding auto-recovered: {tr_finding_title(m.group(1))} "
                f"(lasted {_tr_dur(m.group(2))}, {m.group(3)} rounds without re-trigger)")),
    (re.compile(r"^安全告警: (.+)——疑似中间人或系统重装，采集已拒绝连接；确认后可在设置页重置该主机指纹$"),
     lambda m: (f"Security alert: {m.group(1)} — possible MITM or OS reinstall; "
                "collection refused the connection. Reset the host fingerprint in Settings after confirming")),
    (re.compile(r"^巡检心跳$"), lambda m: "Inspection heartbeat"),
    (re.compile(r"^\[(CRIT|WARN|INFO)\] (.+)$"),
     lambda m: f"[{m.group(1)}] {tr_finding_title(m.group(2))}"),
    (re.compile(r"^agent 完成诊断: (.+)$"),
     lambda m: f"Agent diagnosis completed: {tr_finding_title(m.group(1))}"),
    # 历史半 EN 行：EN 面板期间写入的事件前缀已 EN、嵌套标题仍中文，同样级联翻译
    (re.compile(r"^Agent diagnosis completed: (.+)$"),
     lambda m: f"Agent diagnosis completed: {tr_finding_title(m.group(1))}"),
    (re.compile(r"^Finding auto-recovered: (.+) \(lasted (.+?), (\d+) rounds without re-trigger\)$"),
     lambda m: (f"Finding auto-recovered: {tr_finding_title(m.group(1))} "
                f"(lasted {_tr_dur(m.group(2))}, {m.group(3)} rounds without re-trigger)")),
    (re.compile(r"^AI 叙事失败（诊断继续，规则结论不受影响）: (.+)$"),
     lambda m: f"AI narration failed (diagnosis continues, rule verdicts unaffected): {m.group(1)}"),
    (re.compile(r"^生成健康报告（(.+?)），整体健康分 (\d+)$"),
     lambda m: f"Health report generated ({_REPORT_KIND_EN.get(m.group(1), m.group(1))}), overall score {m.group(2)}"),
    (re.compile(r"^定时报告已生成（每 (\d+) 分钟），整体 (\d+) 分$"),
     lambda m: f"Scheduled report generated (every {m.group(1)} min), overall score {m.group(2)}"),
    (re.compile(r"^模拟执行生效：(.+) 已解除，主机开始恢复$"),
     lambda m: f"Simulated execution took effect: {m.group(1)} cleared, host recovering"),
    (re.compile(r"^提案 #(\d+) 执行(成功|失败)（([^，]+)，(\d+)ms）: (.+)$"),
     lambda m: (f"Proposal #{m.group(1)} executed "
                f"{'successfully' if m.group(2) == '成功' else 'failed'} "
                f"({m.group(3)}, {m.group(4)}ms): {m.group(5)}")),
    (re.compile(r"^发现已确认: (.+)$"),
     lambda m: f"Finding acknowledged: {tr_finding_title(m.group(1))}"),
]

# 提案标题模板（PROPOSALS + _proposal_for 真实主机路径）
_PROPOSAL_TITLE_RULES = [
    (re.compile(r"^清理 journal 与过期日志$"), lambda m: "Clean up journals and expired logs"),
    (re.compile(r"^重启泄漏进程$"), lambda m: "Restart the leaking process"),
    (re.compile(r"^封禁来源 IP 并改密$"), lambda m: "Block source IP and rotate credentials"),
    (re.compile(r"^封禁来源 IP (.+) 并改密$"),
     lambda m: f"Block source IP {m.group(1)} and rotate credentials"),
    (re.compile(r"^重启失败服务$"), lambda m: "Restart the failed service"),
    (re.compile(r"^重启失败服务 (.+)$"), lambda m: f"Restart failed service {m.group(1)}"),
]

# 提案理由模板（rationale 列；command 是命令不翻译）
_PROPOSAL_RATIONALE_RULES = [
    (re.compile(r"^回收 journal 空间并删除 7 天前的压缩日志，不影响在线服务$"),
     lambda m: "Reclaim journal space and delete compressed logs older than 7 days without touching live services"),
    (re.compile(r"^定位到的 worker 进程 RSS 占用 91%，重启释放内存；请先确认业务低峰$"),
     lambda m: "Located worker process RSS at 91% — restart frees memory; confirm an off-peak window first"),
    (re.compile(r"^该 IP 为 Tor 出口节点特征段，先封禁再轮换 root 凭据$"),
     lambda m: "This IP matches a Tor exit-node range — block it first, then rotate root credentials"),
    (re.compile(r"^恢复 failed unit$"), lambda m: "Restore the failed unit"),
    (re.compile(r"^先封禁可疑来源，再轮换 root 凭据$"),
     lambda m: "Block the suspicious source first, then rotate root credentials"),
]


def _tr(text: str | None, rules) -> str:
    """EN 面板下按模板表正则翻译，命中即返回译文，未命中保留原文；zh 面板原样返回。"""
    if lang() != "en" or not text:
        return text or ""
    for pat, fmt in rules:
        m = pat.fullmatch(text)
        if m:
            return fmt(m)
    return text


def tr_finding_title(title: str | None) -> str:
    return _tr(title, _FINDING_TITLE_RULES)


def tr_finding_detail(detail: str | None) -> str:
    return _tr(detail, _FINDING_DETAIL_RULES)


def tr_event_message(message: str | None) -> str:
    return _tr(message, _EVENT_RULES)


def tr_proposal_title(title: str | None) -> str:
    return _tr(title, _PROPOSAL_TITLE_RULES)


def tr_proposal_rationale(text: str | None) -> str:
    return _tr(text, _PROPOSAL_RATIONALE_RULES)


# 历史行嵌套的中文时长单位 → EN（"11 分钟"/"3 小时 5 分"/"2 天"）
_DUR_RULES = [
    (re.compile(r"^(\d+) 分钟$"), lambda m: f"{m.group(1)} min"),
    (re.compile(r"^(\d+) 小时 (\d+) 分$"), lambda m: f"{m.group(1)} h {m.group(2)} min"),
    (re.compile(r"^(\d+) 小时$"), lambda m: f"{m.group(1)} h"),
    (re.compile(r"^(\d+) 天 (\d+) 小时$"), lambda m: f"{m.group(1)} d {m.group(2)} h"),
    (re.compile(r"^(\d+) 天$"), lambda m: f"{m.group(1)} d"),
]


def _tr_dur(s: str) -> str:
    for pat, fn in _DUR_RULES:
        if pat.fullmatch(s or ""):
            return fn(pat.fullmatch(s))
    return s


# ---- 通知留痕（notify_log）读出口兜底 ----
# kind 词表 = notify.send 各调用处的中文 kind；text 嵌套发现标题级联 tr_finding_title
_NOTIFY_KIND = {
    "发现告警": "Finding alert",
    "诊断完成": "Diagnosis completed",
    "告警恢复": "Alert recovered",
    "持续告警": "Ongoing alert",
    "SSH 指纹变更": "SSH host key changed",
    "测试": "Test",
    "测试留痕": "Test log",
}


def tr_notify_kind(kind: str | None) -> str:
    if lang() != "en" or not kind:
        return kind or ""
    return _NOTIFY_KIND.get(kind, kind)


# text 正文：持续告警/恢复通知嵌套发现标题，级联 tr_finding_title；时长单位一并归一
_NOTIFY_TEXT_RULES = [
    (re.compile(r"^(.+?): (.+) \(over (\d+) min without recovery\)$"),
     lambda m: f"{m.group(1)}: {tr_finding_title(m.group(2))} (over {m.group(3)} min without recovery)"),
    (re.compile(r"^(.+?): (.+)（已持续超 (\d+) 分钟未恢复）$"),
     lambda m: f"{m.group(1)}: {tr_finding_title(m.group(2))} (over {m.group(3)} min without recovery)"),
    (re.compile(r"^(.+?) \(lasted (.+?), (\d+) rounds without re-trigger\)$"),
     lambda m: (f"{tr_finding_title(m.group(1))} (lasted {_tr_dur(m.group(2))}, "
                f"{m.group(3)} rounds without re-trigger)")),
    (re.compile(r"^(.+?)（持续 (.+?)，连续 (\d+) 轮未再触发）$"),
     lambda m: (f"{tr_finding_title(m.group(1))} (lasted {_tr_dur(m.group(2))}, "
                f"{m.group(3)} rounds without re-trigger)")),
]


def tr_notify_text(text: str | None) -> str:
    if lang() != "en" or not text:
        return text or ""
    return _tr(text, _NOTIFY_TEXT_RULES)


_NOTIFY_ERROR = {"未配置": "not configured", "quiet（免打扰时段拦截）": "quiet hours (suppressed)"}


def tr_notify_error(error: str | None) -> str:
    if lang() != "en" or not error:
        return error or ""
    return _NOTIFY_ERROR.get(error, error)


# ---- 诊断卡读出口兜底 ----
# card 在 analysis.py 生成处直接 t()（存库语言跟随生成时面板语言），此处负责
# 「历史 zh 卡」在 EN 面板下的显示翻译：字段值全部来自固定模板集，可精确反查。
_CARD_ROOT_CAUSE_RULES = [
    (re.compile(r"^磁盘使用率 (\d+(?:\.\d+)?)% 越过阈值，主要占用来源以深挖输出为准$"),
     lambda m: f"Disk usage {m.group(1)}% crossed the threshold — the main consumer is determined by the deep-dive output"),
    (re.compile(r"^内存使用率 (\d+(?:\.\d+)?)% 越过阈值，占用大头以深挖输出为准$"),
     lambda m: f"Memory usage {m.group(1)}% crossed the threshold — the top consumer is determined by the deep-dive output"),
    (re.compile(r"^CPU 使用率 (\d+(?:\.\d+)?)% 越过阈值，热点进程以深挖输出为准$"),
     lambda m: f"CPU usage {m.group(1)}% crossed the threshold — the hot process is determined by the deep-dive output"),
    (re.compile(r"^load1 ([\d.]+) 越过阈值，通常与 CPU/IO 排队相关$"),
     lambda m: f"load1 {m.group(1)} crossed the threshold — usually related to CPU/IO queuing"),
    (re.compile(r"^来源 IP (.+) 以用户 (.+) 登录成功，不在常见内网段$"),
     lambda m: f"Source IP {m.group(1)} logged in as user {m.group(2)} — outside common intranet ranges"),
    (re.compile(r"^systemd 报告 unit (.+) 处于 failed 状态$"),
     lambda m: f"systemd reports unit {m.group(1)} in a failed state"),
    (re.compile(r"^证书剩余 (\d+) 天，低于告警阈值$"),
     lambda m: f"Certificate has {m.group(1)} days remaining, below the alert threshold"),
    (re.compile(r"^规则引擎确认指标越限，证据见执行轨迹$"),
     lambda m: "The rule engine confirmed a threshold breach; evidence in the execution trace"),
]

_CARD_STEP_LABEL_RULES = [
    (re.compile(r"^定位大文件目录$"), lambda m: "Locate large-file directories"),
    (re.compile(r"^定位高内存进程$"), lambda m: "Locate top-memory processes"),
    (re.compile(r"^审计登录记录$"), lambda m: "Audit login records"),
    (re.compile(r"^查看失败详情$"), lambda m: "Inspect failure details"),
    (re.compile(r"^排查$"), lambda m: "Investigate"),
]

_CARD_CONFIDENCE = {"高": "High", "中": "Medium", "低": "Low"}


def tr_card(card: dict | None, chain_map: dict[str, list[str]] | None = None,
            root_map: dict[str, str] | None = None) -> dict | None:
    """EN 面板下翻译历史中文诊断卡；zh 面板原样返回。chain_map/root_map 由
    analysis.tr_card 传入（反向映射表在 analysis 模块，避免本模块反向依赖它）。"""
    if card is None or lang() != "en":
        return card
    out = dict(card)
    rc = out.get("root_cause")
    if rc:
        if root_map and rc in root_map:
            out["root_cause"] = root_map[rc]
        else:
            out["root_cause"] = _tr(rc, _CARD_ROOT_CAUSE_RULES)
    if chain_map and isinstance(out.get("chain"), list):
        out["chain"] = [chain_map.get(s, s) for s in out["chain"]]
    if isinstance(out.get("steps"), list):
        for s in out["steps"]:
            if isinstance(s, dict) and s.get("label"):
                s["label"] = _tr(s["label"], _CARD_STEP_LABEL_RULES)
    if out.get("confidence") in _CARD_CONFIDENCE:
        out["confidence"] = _CARD_CONFIDENCE[out["confidence"]]
    return out
