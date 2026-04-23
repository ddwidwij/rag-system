"""
checker.py — 文档规则检查器
对 Markdown 文档执行静态规则分析，检测常见格式和质量问题。

覆盖项：
  - 重复词语（的的、了了等）
  - 标题格式（# 后缺空格、空标题）
  - 空章节
  - 断裂的相对链接
  - 连续空行过多
  - 过长段落
  - 全角/半角数字混用
  - Note/Warning/Caution 使用规范
  - 中英文之间缺少空格（Pangu 风格）
  - 连续重复行
  - 术语一致性（从配置文件加载）
  - 有序列表编号不连续

用法:
    python checker.py docs/                   # 检查目录下所有 .md 文件
    python checker.py docs/接口测试.md        # 检查单个文件
    python checker.py docs/ --json            # 输出 JSON 格式
    python checker.py docs/ --level warning   # 只显示 warning 及以上级别
    python checker.py docs/ --config my.json  # 自定义配置文件
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

# ── 问题级别 ────────────────────────────────────────────────────────────────
LEVEL_ERROR   = "error"    # 明确错误，必须修改
LEVEL_WARNING = "warning"  # 疑似问题，建议检查
LEVEL_INFO    = "info"     # 提示性改进建议

_LEVEL_ORDER = {LEVEL_ERROR: 0, LEVEL_WARNING: 1, LEVEL_INFO: 2}

# ── 问题类别 ────────────────────────────────────────────────────────────────
CAT_TYPO        = "重复词语"
CAT_FORMAT      = "格式规范"
CAT_PUNCTUATION = "标点符号"
CAT_LINK        = "链接有效性"
CAT_STRUCTURE   = "文档结构"
CAT_TERM        = "术语一致性"
CAT_READABILITY = "可读性"
CAT_NOTE        = "提示框规范"

# ── ANSI 颜色（终端输出） ───────────────────────────────────────────────────
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_GREEN  = "\033[32m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"

_LEVEL_COLOR = {
    LEVEL_ERROR:   _RED,
    LEVEL_WARNING: _YELLOW,
    LEVEL_INFO:    _CYAN,
}

# ── 默认配置 ────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG: dict = {
    # 术语一致性：preferred_term → [非推荐写法列表]
    "preferred_terms": {
        "接口": ["API", "api", "Api"],
        "请求参数": ["request param", "request parameter"],
        "响应": ["response", "Response"],
        "测试用例": ["test case", "testcase", "TestCase"],
        "断言": ["assert", "assertion"],
        "预期结果": ["expected result", "expect result"],
        "前置条件": ["precondition", "pre-condition"],
    },
    # 段落字符数超过此值触发警告
    "max_paragraph_length": 400,
    # 连续空行超过此数量触发警告
    "max_blank_lines": 2,
    # Note/Warning/Caution 关键词（中文）
    "note_keywords": ["注意", "警告", "危险", "提示", "说明"],
}


# ── 数据结构 ────────────────────────────────────────────────────────────────
@dataclass
class Issue:
    line: int          # 1-based 行号
    col: int           # 1-based 列号（0 表示未知）
    level: str         # error / warning / info
    category: str      # 问题类别
    message: str       # 问题描述
    context: str = ""  # 出错行的文本片段
    suggestion: str = ""  # 修改建议


@dataclass
class CheckResult:
    file_path: str
    issues: List[Issue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.level == LEVEL_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.level == LEVEL_WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.level == LEVEL_INFO)

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "summary": {
                "error":   self.error_count,
                "warning": self.warning_count,
                "info":    self.info_count,
                "total":   len(self.issues),
            },
            "issues": [asdict(i) for i in self.issues],
        }


# ── Markdown 预处理工具 ──────────────────────────────────────────────────────
def _find_code_block_lines(lines: List[str]) -> Set[int]:
    """返回处于围栏代码块（```）内部的行号集合（1-based，含围栏行本身）。"""
    in_block = False
    code_lines: Set[int] = set()
    fence_pattern = re.compile(r"^(`{3,}|~{3,})")
    for i, line in enumerate(lines, 1):
        if fence_pattern.match(line.strip()):
            in_block = not in_block
            code_lines.add(i)
        elif in_block:
            code_lines.add(i)
    return code_lines


def _strip_inline_markup(text: str) -> str:
    """去除行内 Markdown 标记，返回接近纯文本的字符串（用于文本内容检查）。"""
    text = re.sub(r"`[^`\n]+`", "  ", text)                       # 内联代码
    text = re.sub(r"!\[[^\]]*\]\([^\)]*\)", " ", text)            # 图片
    text = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", text)         # 链接 → 保留文字
    text = re.sub(r"\*{1,2}([^*\n]+)\*{1,2}", r"\1", text)       # 粗体/斜体
    text = re.sub(r"_{1,2}([^_\n]+)_{1,2}", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text)                        # 标题 #
    text = re.sub(r"^[-*+]\s+", "", text)                         # 无序列表
    text = re.sub(r"^\d+\.\s+", "", text)                         # 有序列表
    text = re.sub(r"^>\s*", "", text)                             # 引用
    return text


# ── 各项规则实现 ─────────────────────────────────────────────────────────────

# 规则 1：重复词语
# 只检测 2-4 个汉字组成的词组被连续重复（如"进行进行"），
# 不检测单字叠词（妈妈/天天/常常 是合法汉语构词）和英文字符（避免 HTTP/boolean 误报）。
_REPEAT_WORD_RE = re.compile(
    r"([\u4e00-\u9fff]{2,4})\1"
)
# 此外额外检测明显错误的单字重复：助词/副词 连续出现
_REPEAT_PARTICLE_RE = re.compile(
    r"([的了着过地得也都就还])\1"
)

def check_repeated_words(lines: List[str], code_lines: Set[int], _cfg: dict) -> List[Issue]:
    issues = []
    for lineno, line in enumerate(lines, 1):
        if lineno in code_lines:
            continue
        plain = _strip_inline_markup(line)
        # 2-4 汉字组成的短语重复
        for m in _REPEAT_WORD_RE.finditer(plain):
            word = m.group(1)
            issues.append(Issue(
                line=lineno, col=m.start() + 1,
                level=LEVEL_ERROR, category=CAT_TYPO,
                message=f'疑似重复词语："{word}{word}"',
                context=line.rstrip()[:100],
                suggestion=f'检查是否误写重复，应为："{word}"',
            ))
        # 助词/副词单字重复（的的、了了等）
        for m in _REPEAT_PARTICLE_RE.finditer(plain):
            char = m.group(1)[0]
            issues.append(Issue(
                line=lineno, col=m.start() + 1,
                level=LEVEL_ERROR, category=CAT_TYPO,
                message=f'助词/副词重复："{char}{char}"',
                context=line.rstrip()[:100],
                suggestion=f'删除多余的"{char}"',
            ))
    return issues


# 规则 2：标题格式（# 后缺空格 / 空标题）
_HEADING_NO_SPACE_RE = re.compile(r"^(#{1,6})([^#\s\n])")
_HEADING_EMPTY_RE    = re.compile(r"^(#{1,6})\s*$")

def check_heading_format(lines: List[str], code_lines: Set[int], _cfg: dict) -> List[Issue]:
    issues = []
    for lineno, line in enumerate(lines, 1):
        if lineno in code_lines:
            continue
        stripped = line.rstrip()
        if _HEADING_NO_SPACE_RE.match(stripped):
            issues.append(Issue(
                line=lineno, col=1,
                level=LEVEL_ERROR, category=CAT_FORMAT,
                message="标题 # 号后缺少空格",
                context=stripped[:80],
                suggestion=f'改为："{re.sub(r"^(#{1,6})", r"\\1 ", stripped)}"',
            ))
        elif _HEADING_EMPTY_RE.match(stripped):
            issues.append(Issue(
                line=lineno, col=1,
                level=LEVEL_ERROR, category=CAT_STRUCTURE,
                message="标题内容为空",
                context=stripped[:80],
            ))
    return issues


# 规则 3：空章节（标题后紧跟下一个同级或更高级标题，中间无内容）
def check_empty_sections(lines: List[str], code_lines: Set[int], _cfg: dict) -> List[Issue]:
    issues = []
    heading_re = re.compile(r"^(#{1,6})\s+\S")
    prev_heading_lineno = -1
    prev_heading_level  = 0
    prev_heading_text   = ""
    has_content_since_heading = False

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        m = heading_re.match(stripped) if lineno not in code_lines else None
        if m:
            current_level = len(m.group(1))
            if prev_heading_lineno > 0 and not has_content_since_heading:
                issues.append(Issue(
                    line=prev_heading_lineno, col=1,
                    level=LEVEL_WARNING, category=CAT_STRUCTURE,
                    message=f'章节 "{prev_heading_text}" 内容为空',
                    context=prev_heading_text,
                    suggestion="在标题下方补充内容，或删除该标题",
                ))
            prev_heading_lineno    = lineno
            prev_heading_level     = current_level
            prev_heading_text      = stripped
            has_content_since_heading = False
        elif stripped and lineno not in code_lines:
            has_content_since_heading = True

    return issues


# 规则 4：断裂的相对链接
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^\)]+)\)")

def check_broken_links(lines: List[str], code_lines: Set[int], _cfg: dict, file_path: Optional[Path] = None) -> List[Issue]:
    issues = []
    if file_path is None:
        return issues

    base_dir = file_path.parent
    for lineno, line in enumerate(lines, 1):
        if lineno in code_lines:
            continue
        for m in _MD_LINK_RE.finditer(line):
            url = m.group(2).split("#")[0].strip()  # 去掉锚点
            if not url:
                continue
            # 跳过绝对链接和锚点链接
            if url.startswith(("http://", "https://", "ftp://", "mailto:", "/")):
                continue
            target = (base_dir / url).resolve()
            if not target.exists():
                issues.append(Issue(
                    line=lineno, col=m.start() + 1,
                    level=LEVEL_ERROR, category=CAT_LINK,
                    message=f'链接目标不存在："{url}"',
                    context=line.rstrip()[:100],
                    suggestion=f"确认文件路径 {url} 是否正确",
                ))
    return issues


# 规则 5：连续空行过多
def check_consecutive_blank_lines(lines: List[str], code_lines: Set[int], cfg: dict) -> List[Issue]:
    issues = []
    max_blank = cfg.get("max_blank_lines", 2)
    blank_count = 0
    blank_start = 0

    for lineno, line in enumerate(lines, 1):
        if lineno in code_lines:
            blank_count = 0
            continue
        if line.strip() == "":
            if blank_count == 0:
                blank_start = lineno
            blank_count += 1
            if blank_count > max_blank:
                issues.append(Issue(
                    line=blank_start, col=0,
                    level=LEVEL_WARNING, category=CAT_FORMAT,
                    message=f"连续空行超过 {max_blank} 行（共 {blank_count} 行）",
                    context="",
                    suggestion=f"减少连续空行至不超过 {max_blank} 行",
                ))
                blank_count = 0  # 避免重复报告
        else:
            blank_count = 0

    return issues


# 规则 6：过长段落
def check_long_paragraphs(lines: List[str], code_lines: Set[int], cfg: dict) -> List[Issue]:
    issues = []
    max_len = cfg.get("max_paragraph_length", 400)
    para_lines: List[Tuple[int, str]] = []

    def flush_para():
        if not para_lines:
            return
        text = "".join(t for _, t in para_lines)
        if len(text) > max_len:
            start_lineno = para_lines[0][0]
            issues.append(Issue(
                line=start_lineno, col=0,
                level=LEVEL_INFO, category=CAT_READABILITY,
                message=f"段落过长（{len(text)} 字符，建议不超过 {max_len}）",
                context=text[:80] + "…",
                suggestion="将长段落拆分为多个较短段落",
            ))

    for lineno, line in enumerate(lines, 1):
        if lineno in code_lines:
            flush_para()
            para_lines = []
            continue
        stripped = line.strip()
        if stripped == "":
            flush_para()
            para_lines = []
        elif re.match(r"^#{1,6}\s", stripped):
            flush_para()
            para_lines = []
        else:
            para_lines.append((lineno if not para_lines else para_lines[0][0], stripped))

    flush_para()
    return issues


# 规则 7：全角 / 半角数字混用（同一文档中出现两种写法）
_FULLWIDTH_DIGIT_RE = re.compile(r"[０-９]")
_HALFWIDTH_DIGIT_RE = re.compile(r"[0-9]")

def check_mixed_fullwidth_digits(lines: List[str], code_lines: Set[int], _cfg: dict) -> List[Issue]:
    issues = []
    has_full = any(
        _FULLWIDTH_DIGIT_RE.search(line)
        for i, line in enumerate(lines, 1) if i not in code_lines
    )
    has_half = any(
        _HALFWIDTH_DIGIT_RE.search(line)
        for i, line in enumerate(lines, 1) if i not in code_lines
    )
    if has_full and has_half:
        # 找第一处全角数字所在行
        for lineno, line in enumerate(lines, 1):
            if lineno in code_lines:
                continue
            m = _FULLWIDTH_DIGIT_RE.search(line)
            if m:
                issues.append(Issue(
                    line=lineno, col=m.start() + 1,
                    level=LEVEL_WARNING, category=CAT_FORMAT,
                    message=f'文档中混用了全角数字（如 "{m.group()}"）和半角数字',
                    context=line.rstrip()[:80],
                    suggestion="统一使用半角数字（0-9）",
                ))
                break
    return issues


# 规则 8：Note / Warning / Caution 格式规范
# 规范：中文文档中应使用 "**注意**："、"**警告**："等加粗格式，而不是裸文字
def check_note_format(lines: List[str], code_lines: Set[int], cfg: dict) -> List[Issue]:
    issues = []
    keywords = cfg.get("note_keywords", ["注意", "警告", "危险", "提示", "说明"])
    # 非规范写法：行首直接出现关键词，但未加粗
    bare_re = re.compile(
        r"^(" + "|".join(re.escape(k) for k in keywords) + r")[：:]\s*\S"
    )
    # 规范写法：**注意**：
    proper_re = re.compile(
        r"^\*{1,2}(" + "|".join(re.escape(k) for k in keywords) + r")\*{1,2}[：:]"
    )
    for lineno, line in enumerate(lines, 1):
        if lineno in code_lines:
            continue
        stripped = line.strip()
        if bare_re.match(stripped) and not proper_re.match(stripped):
            issues.append(Issue(
                line=lineno, col=1,
                level=LEVEL_WARNING, category=CAT_NOTE,
                message=f'提示框关键词未加粗："{stripped[:30]}"',
                context=stripped[:80],
                suggestion='使用加粗格式，如：**注意**：内容',
            ))
    return issues


# 规则 9：中英文之间缺少空格（Pangu 风格）
# 中文字符紧跟英文字母/数字，或英文字母/数字紧跟中文字符
_CJK_LATIN_RE = re.compile(
    r"([\u4e00-\u9fff\u3400-\u4dbf])([A-Za-z0-9])|([A-Za-z0-9])([\u4e00-\u9fff\u3400-\u4dbf])"
)

def check_cjk_latin_spacing(lines: List[str], code_lines: Set[int], _cfg: dict) -> List[Issue]:
    issues = []
    reported_lines: Set[int] = set()
    for lineno, line in enumerate(lines, 1):
        if lineno in code_lines:
            continue
        plain = _strip_inline_markup(line)
        if _CJK_LATIN_RE.search(plain) and lineno not in reported_lines:
            m = _CJK_LATIN_RE.search(plain)
            if m:
                issues.append(Issue(
                    line=lineno, col=m.start() + 1,
                    level=LEVEL_INFO, category=CAT_FORMAT,
                    message="中英文之间建议添加空格",
                    context=line.rstrip()[:100],
                    suggestion='在中文与英文/数字之间插入一个空格，如"接口 API"',
                ))
                reported_lines.add(lineno)
    return issues


# 规则 10：连续重复行（相邻行文字完全相同且非空）
def check_consecutive_duplicate_lines(lines: List[str], code_lines: Set[int], _cfg: dict) -> List[Issue]:
    issues = []
    for lineno in range(2, len(lines) + 1):
        if lineno in code_lines or (lineno - 1) in code_lines:
            continue
        prev = lines[lineno - 2].strip()
        curr = lines[lineno - 1].strip()
        if prev and curr and prev == curr and len(prev) > 5:
            issues.append(Issue(
                line=lineno, col=1,
                level=LEVEL_WARNING, category=CAT_TYPO,
                message="与上一行完全重复",
                context=curr[:80],
                suggestion="删除重复行",
            ))
    return issues


# 规则 11：有序列表编号不连续
_OL_ITEM_RE = re.compile(r"^(\d+)\.\s")

def check_numbered_list(lines: List[str], code_lines: Set[int], _cfg: dict) -> List[Issue]:
    issues = []
    expected = None

    for lineno, line in enumerate(lines, 1):
        if lineno in code_lines:
            expected = None
            continue
        m = _OL_ITEM_RE.match(line.lstrip())
        if m:
            num = int(m.group(1))
            if expected is None:
                expected = num + 1
            elif num != expected:
                issues.append(Issue(
                    line=lineno, col=1,
                    level=LEVEL_WARNING, category=CAT_FORMAT,
                    message=f"有序列表编号不连续：期望 {expected}，实际 {num}",
                    context=line.rstrip()[:80],
                    suggestion=f"将编号改为 {expected}",
                ))
                expected = num + 1
            else:
                expected += 1
        else:
            if line.strip() == "":
                expected = None  # 空行重置列表计数

    return issues


# 规则 12：术语一致性（从配置中加载首选术语表）
def check_terminology(lines: List[str], code_lines: Set[int], cfg: dict) -> List[Issue]:
    issues = []
    preferred_terms: Dict[str, List[str]] = cfg.get("preferred_terms", {})

    for preferred, alternatives in preferred_terms.items():
        for alt in alternatives:
            alt_re = re.compile(re.escape(alt))
            for lineno, line in enumerate(lines, 1):
                if lineno in code_lines:
                    continue
                plain = _strip_inline_markup(line)
                m = alt_re.search(plain)
                if m:
                    issues.append(Issue(
                        line=lineno, col=m.start() + 1,
                        level=LEVEL_INFO, category=CAT_TERM,
                        message=f'建议使用"{preferred}"替代"{alt}"（术语统一）',
                        context=line.rstrip()[:80],
                        suggestion=f'将 "{alt}" 改为 "{preferred}"',
                    ))

    return issues


# ── 检查器主函数 ─────────────────────────────────────────────────────────────
RuleFunc = Callable[[List[str], Set[int], dict], List[Issue]]

_ALL_RULES: List[Tuple[str, RuleFunc]] = [
    ("重复词语",         check_repeated_words),
    ("标题格式",         check_heading_format),
    ("空章节",           check_empty_sections),
    ("连续空行",         check_consecutive_blank_lines),
    ("过长段落",         check_long_paragraphs),
    ("全角数字混用",     check_mixed_fullwidth_digits),
    ("Note格式",         check_note_format),
    ("中英文间距",       check_cjk_latin_spacing),
    ("连续重复行",       check_consecutive_duplicate_lines),
    ("列表编号连续性",   check_numbered_list),
    ("术语一致性",       check_terminology),
]


def check_file(file_path: Path, cfg: Optional[dict] = None) -> CheckResult:
    """对单个 Markdown 文件执行所有规则检查，返回 CheckResult。"""
    if cfg is None:
        cfg = _DEFAULT_CONFIG

    result = CheckResult(file_path=str(file_path))
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        result.issues.append(Issue(
            line=0, col=0, level=LEVEL_ERROR,
            category=CAT_FORMAT,
            message="文件编码不是 UTF-8，无法解析",
        ))
        return result

    lines = text.splitlines(keepends=True)
    code_lines = _find_code_block_lines(lines)

    for _rule_name, rule_fn in _ALL_RULES:
        new_issues = rule_fn(lines, code_lines, cfg)
        result.issues.extend(new_issues)

    # 链接检查需要传入 file_path，单独处理
    result.issues.extend(
        check_broken_links(lines, code_lines, cfg, file_path=file_path)
    )

    # 按行号排序
    result.issues.sort(key=lambda i: (i.line, i.col))
    return result


def check_directory(dir_path: Path, cfg: Optional[dict] = None, glob: str = "**/*.md") -> List[CheckResult]:
    """递归检查目录下所有 Markdown 文件。"""
    results = []
    md_files = sorted(dir_path.glob(glob))
    if not md_files:
        print(f"[INFO] 未在 {dir_path} 中找到任何 Markdown 文件")
        return results
    for f in md_files:
        results.append(check_file(f, cfg))
    return results


def load_config(config_path: Optional[Path]) -> dict:
    """加载配置文件，未指定时使用默认配置。"""
    if config_path is None:
        # 尝试从项目根目录加载默认配置
        default_cfg_path = Path(__file__).parent / "checker_config.json"
        if default_cfg_path.exists():
            config_path = default_cfg_path
        else:
            return _DEFAULT_CONFIG.copy()

    with config_path.open(encoding="utf-8") as f:
        user_cfg = json.load(f)

    # 合并：用户配置覆盖默认值
    merged = _DEFAULT_CONFIG.copy()
    merged.update(user_cfg)
    return merged


# ── 终端输出 ─────────────────────────────────────────────────────────────────
def _level_label(level: str) -> str:
    color = _LEVEL_COLOR.get(level, "")
    label = f"[{level.upper():7s}]"
    return f"{_BOLD}{color}{label}{_RESET}"


def print_result(result: CheckResult, min_level: str = LEVEL_INFO) -> None:
    """将 CheckResult 以彩色文字打印到终端。"""
    min_order = _LEVEL_ORDER[min_level]
    visible = [i for i in result.issues if _LEVEL_ORDER[i.level] <= min_order]

    if not visible:
        print(f"  {_GREEN}✓ 无问题{_RESET}  {_DIM}{result.file_path}{_RESET}")
        return

    print(f"\n{_BOLD}{'─' * 70}{_RESET}")
    print(f"{_BOLD}{result.file_path}{_RESET}")
    print(f"  {_RED}错误 {result.error_count}{_RESET}  "
          f"{_YELLOW}警告 {result.warning_count}{_RESET}  "
          f"{_CYAN}提示 {result.info_count}{_RESET}")
    print(f"{'─' * 70}")

    for issue in visible:
        loc = f"{issue.line}:{issue.col}" if issue.col else str(issue.line)
        print(f"  {_level_label(issue.level)} {_DIM}行{loc:6s}{_RESET} "
              f"[{issue.category}] {issue.message}")
        if issue.context:
            ctx = issue.context[:90]
            print(f"    {_DIM}  → {ctx}{_RESET}")
        if issue.suggestion:
            print(f"    {_CYAN}  建议：{issue.suggestion}{_RESET}")


def print_summary(results: List[CheckResult]) -> None:
    """打印所有文件的汇总统计。"""
    total_errors   = sum(r.error_count   for r in results)
    total_warnings = sum(r.warning_count for r in results)
    total_infos    = sum(r.info_count    for r in results)
    total_issues   = total_errors + total_warnings + total_infos

    print(f"\n{'═' * 70}")
    print(f"{_BOLD}检查完成：{len(results)} 个文件{_RESET}")
    print(f"  {_RED}错误 {total_errors}{_RESET}  "
          f"{_YELLOW}警告 {total_warnings}{_RESET}  "
          f"{_CYAN}提示 {total_infos}{_RESET}  "
          f"共 {total_issues} 条问题")
    print(f"{'═' * 70}")


# ── CLI 入口 ─────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Markdown 文档规则检查器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="要检查的 Markdown 文件或目录路径")
    parser.add_argument(
        "--level", choices=["error", "warning", "info"], default="info",
        help="只显示该级别及以上的问题（默认 info = 全部显示）",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="以 JSON 格式输出结果（便于程序化处理）",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="自定义配置文件路径（JSON 格式，默认使用 checker_config.json）",
    )
    args = parser.parse_args()

    target = Path(args.path)
    if not target.exists():
        print(f"错误：路径不存在：{args.path}", file=sys.stderr)
        return 2

    cfg = load_config(args.config)

    if target.is_dir():
        results = check_directory(target, cfg)
    elif target.suffix.lower() == ".md":
        results = [check_file(target, cfg)]
    else:
        print(f"错误：不支持的文件类型，请指定 .md 文件或目录", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
    else:
        for result in results:
            print_result(result, min_level=args.level)
        print_summary(results)

    # 若存在 error 级别问题，退出码为 1（便于 CI 集成）
    has_errors = any(r.error_count > 0 for r in results)
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
