"""L0/L1 敏感内容检测：内置规则 + 本地正则/词匹配（不联网）。

规则是“提醒”，不是内容判定；命中只在本机展示，不上传 relay。
内置规则由代码维护，数据库只保存启用/屏蔽状态；自定义词由用户维护。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    label: str
    category: str
    severity: str  # high / medium / low
    kind: str      # word / regex
    pattern: str
    scope: str = "all"  # all / path
    validator: str = ""
    version: int = 1


BUILTIN_RULES: tuple[RuleSpec, ...] = (
    # ---- 凭证（高置信） ----
    RuleSpec("builtin.cred.private_key", "私钥块", "凭证", "high", "regex",
             r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    RuleSpec("builtin.cred.aws_key", "AWS Access Key", "凭证", "high", "regex",
             r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    RuleSpec("builtin.cred.github_token", "GitHub Token", "凭证", "high", "regex",
             r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    RuleSpec("builtin.cred.google_api_key", "Google API Key", "凭证", "high", "regex",
             r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    RuleSpec("builtin.cred.openai_key", "API Key（sk-）", "凭证", "high", "regex",
             r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    RuleSpec("builtin.cred.jwt", "JWT", "凭证", "high", "regex",
             r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    RuleSpec("builtin.cred.conn_string", "带密码的连接串", "凭证", "high", "regex",
             r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:@]+:[^\s@]+@"),
    RuleSpec("builtin.cred.assignment", "密码/密钥赋值", "凭证", "medium", "regex",
             r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*['\"]?[^\s'\"]{4,}"),
    # ---- 个人身份信息（高/中置信） ----
    RuleSpec("builtin.pii.cn_id", "身份证号", "个人信息", "high", "regex",
             r"(?<!\d)\d{17}[\dXx](?!\d)", validator="cn_id"),
    RuleSpec("builtin.pii.bank_card", "银行卡号", "个人信息", "high", "regex",
             r"(?<!\d)\d{16,19}(?!\d)", validator="luhn"),
    RuleSpec("builtin.pii.phone", "手机号", "个人信息", "medium", "regex",
             r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    RuleSpec("builtin.pii.email", "邮箱地址", "个人信息", "low", "regex",
             r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # ---- 业务敏感（中置信；靠上下文和人工确认） ----
    RuleSpec("builtin.biz.compensation", "薪酬/绩效/股权", "人事", "medium", "regex",
             r"(?:薪酬|薪资|工资|绩效|奖金|股权|期权|年终奖)"),
    RuleSpec("builtin.biz.customer_list", "客户名单", "客户", "medium", "regex",
             r"(?:客户名单|客户清单|客户联系方式|客户台账)"),
    RuleSpec("builtin.biz.contract_amount", "合同金额/报价", "合同", "medium", "regex",
             r"(?:合同金额|合同总价|合同价款|报价单|成交价|采购价)"),
    RuleSpec("builtin.biz.ma", "并购/尽调", "法务", "medium", "regex",
             r"(?:并购|收购|尽职调查|尽调)"),
    RuleSpec("builtin.biz.medical", "医疗/体检", "个人健康", "medium", "regex",
             r"(?:病历|体检报告|诊断报告|处方|就医记录)"),
    RuleSpec("builtin.biz.legal", "诉讼/保密", "法务", "medium", "regex",
             r"(?:诉讼|仲裁|律师函|保密协议|竞业限制)"),
    RuleSpec("builtin.biz.confidential", "未公开/机密", "内部信息", "medium", "regex",
             r"(?:未公开|内部机密|商业秘密|绝密|机密文件)"),
    RuleSpec("builtin.biz.password_word", "密码/口令/密钥", "凭证", "low", "regex",
             r"(?:密码(?!学)|口令|密钥|私钥|凭证)"),
    RuleSpec("builtin.biz.personal_terms", "身份证/银行卡等词", "个人信息", "medium", "regex",
             r"(?:身份证|银行卡号|护照号|社保号)"),
    # ---- L0 路径/标题（只匹配路径和标题，避免正文泛词误报） ----
    RuleSpec("builtin.path.hr", "人事/薪酬路径", "路径", "low", "regex",
             r"(?:薪酬|薪资|工资|绩效|人事|员工|离职|裁员|招聘)", scope="path"),
    RuleSpec("builtin.path.finance", "财务/报销路径", "路径", "low", "regex",
             r"(?:财务|报销|发票|预算|账务|审计)", scope="path"),
    RuleSpec("builtin.path.legal", "合同/法务路径", "路径", "low", "regex",
             r"(?:合同|协议|法务|诉讼|保密)", scope="path"),
    RuleSpec("builtin.path.secret", "凭证/密钥路径", "路径", "low", "regex",
             r"(?i)(?:secret|credential|password|private[_-]?key|api[_-]?key)", scope="path"),
)


def _cn_id_valid(value: str) -> bool:
    if len(value) != 18:
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = "10X98765432"
    try:
        total = sum(int(value[i]) * weights[i] for i in range(17))
    except ValueError:
        return False
    return value[-1].upper() == checks[total % 11]


def _luhn_valid(value: str) -> bool:
    digits = [int(ch) for ch in value if ch.isdigit()]
    if len(digits) < 12:
        return False
    if len(set(digits)) == 1:
        return False  # 全 0 / 全 8 等占位串不能算银行卡
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _mask_hit(value: str, rule: dict) -> str:
    rid = rule.get("rule_id", "")
    if rid.endswith("private_key"):
        return "-----BEGIN … PRIVATE KEY-----"
    if len(value) < 6:
        return value
    if rid.endswith("cn_id") and len(value) == 18:
        return value[:6] + "********" + value[-4:]
    if rid.endswith("phone") and len(value) == 11:
        return value[:3] + "****" + value[-4:]
    if rid.endswith("bank_card") and len(value) >= 8:
        return value[:4] + "*" * (len(value) - 8) + value[-4:]
    if rid.endswith("email") and "@" in value:
        name, domain = value.split("@", 1)
        return (name[:2] + "***@" + domain) if name else ("***@" + domain)
    return value[:4] + "…" + value[-2:]


def _redact_tokens(s: str) -> str:
    patterns = (
        (r"github_pat_[A-Za-z0-9_]{8,}", "github_pat_…"),
        (r"gh[pousr]_[A-Za-z0-9]{8,}", "gh…"),
        (r"sk-[A-Za-z0-9_-]{8,}", "sk-…"),
        (r"AIza[0-9A-Za-z_-]{12,}", "AIza…"),
        (r"AKIA[0-9A-Z]{12,}", "AKIA…"),
        (r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", "eyJ…"),
        (r"\b[A-Za-z0-9_-]{20,}\b", "…"),
        (r"\b\d{11,}\b", "…"),
    )
    for pat, repl in patterns:
        s = re.sub(pat, repl, s)
    return s


def _excerpt(text: str, start: int, end: int, value: str = "", rule: Optional[dict] = None) -> str:
    left = max(0, start - 55)
    right = min(len(text), end + 55)
    s = re.sub(r"\s+", " ", text[left:right]).strip()
    if value and rule is not None and rule.get("category") in ("凭证", "个人信息"):
        s = s.replace(value, _mask_hit(value, rule), 1)
    s = _redact_tokens(s)
    return s[:180]


def _word_pattern(word: str) -> re.Pattern[str]:
    # 英文/数字按词边界，中文按字面子串；避免 token 命中 tokenizer。
    if re.fullmatch(r"[A-Za-z0-9_.+-]+", word):
        return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", re.I)
    return re.compile(re.escape(word), re.I)


def scan_text(text: str, path_text: str, rules: Iterable[dict]) -> tuple[list[dict], str]:
    """返回 (hits, signature)。

    rules 是数据库行（含 rule_id/kind/pattern/version/severity/scope）。
    只返回启用的规则；低置信规则也会记录，但是否亮红点由上层决定。
    """
    rules = list(rules)
    hits: list[dict] = []
    for rule in rules:
        if not int(rule.get("enabled", 1)):
            continue
        scope = rule.get("scope") or "all"
        hay = path_text if scope == "path" else (path_text + "\n" + text)
        pattern = rule.get("pattern") or ""
        kind = rule.get("kind") or "word"
        try:
            if kind == "word":
                rx = _word_pattern(pattern)
            else:
                rx = re.compile(pattern, re.I)
        except re.error:
            continue
        validator = (rule.get("validator") or "").strip()
        count = 0
        first: Optional[re.Match[str]] = None
        for m in rx.finditer(hay):
            value = m.group(0)
            if validator == "cn_id" and not _cn_id_valid(value):
                continue
            if validator == "luhn" and not _luhn_valid(value):
                continue
            count += 1
            if first is None:
                first = m
        if count and first is not None:
            hits.append({
                "rule_id": rule["rule_id"],
                "count": count,
                "excerpt": _excerpt(hay, first.start(), first.end(), first.group(0), rule),
                "severity": rule.get("severity") or "medium",
            })
    keys = sorted(f"{h['rule_id']}:{next((r.get('version', 1) for r in rules if r.get('rule_id') == h['rule_id']), 1)}" for h in hits)
    sig = hashlib.sha256("|".join(keys).encode("utf-8")).hexdigest()[:32] if keys else ""
    return hits, sig


def rule_key(rule: dict) -> str:
    return f"{rule.get('rule_id')}:{rule.get('version', 1)}"
