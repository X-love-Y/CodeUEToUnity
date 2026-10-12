# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml"]
# ///
"""
UE -> Unity 代码迁移工具 (MVP v0.1)

用法:
    uv run migrate.py all                 # 全流程: scan -> plan -> translate -> verify -> report
    uv run migrate.py scan                # 只扫描, 输出翻译单元和映射覆盖率
    uv run migrate.py plan                # 生成每个单元的翻译任务包
    uv run migrate.py translate           # 调模型翻译 (provider=api) 或读取人工响应 (provider=agent)
    uv run migrate.py verify              # 用真实 Unity 程序集编译生成的 C#
    uv run migrate.py report              # 生成迁移报告

设计原则:
    - 每个阶段只做一件事, 中间产物全部落在磁盘上, 可以单独重跑
    - 所有中间产物是 JSON/YAML/Markdown, 人和模型都能读
    - 映射表是数据不是代码, 加一行就多一条能力
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------

def log(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    log(f"\n=== {msg} ===")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def read_json(path: Path):
    return json.loads(read_text(path))


def load_config() -> dict:
    cfg = yaml.safe_load(read_text(ROOT / "config.yaml"))
    paths = cfg["paths"]
    for key in ("input", "work", "output", "reports"):
        paths[key] = ROOT / paths[key]
        paths[key].mkdir(parents=True, exist_ok=True)
    paths["mappings"] = ROOT / paths["mappings"]
    paths["prompts"] = ROOT / paths["prompts"]
    # 允许用环境变量临时覆盖 provider, 便于在 agent / api 之间切换测试,
    # 不必反复改配置文件:  $env:MIGRATE_PROVIDER = "api"
    cfg["provider"] = os.environ.get("MIGRATE_PROVIDER", cfg["provider"])
    return cfg


def load_mappings(cfg: dict):
    data = yaml.safe_load(read_text(cfg["paths"]["mappings"]))
    entries = data.get("entries") or []
    by_id = {e["id"]: e for e in entries}
    return entries, by_id


def render(template: str, values: dict) -> str:
    out = template
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", str(val))
    return out


# ---------------------------------------------------------------------------
# 阶段 1: scan —— 找出翻译单元, 分析映射覆盖率
# ---------------------------------------------------------------------------

# UE 命名约定: 类名前缀 A(AActor) / U(UObject) / F(结构) / T(模板) / E(枚举) / I(接口)
UE_PREFIXED_RE = re.compile(r"\b[AUFTEI][A-Z][A-Za-z0-9_]{2,}\b")
# 全大写宏 / 常量
UE_MACRO_RE = re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b")
# UE 反射标记
REFLECTION_RE = re.compile(r"\bU(CLASS|STRUCT|ENUM|INTERFACE)\s*\(")
TYPE_DECL_RE = re.compile(
    r"\b(?:class|struct)\s+(?:\w+_API\s+)?([AUFTEI]?\w+)\b|\benum\s+class\s+(\w+)\b"
)
# 注释: 分析符号前先剥离, 否则注释里的英文单词会污染结果
COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
# 模块导出宏, 如 MYGAME_API
EXPORT_MACRO_RE = re.compile(r"^[A-Z][A-Z0-9_]*_API$")

# 不是需要映射的符号(语言层面的宏本身 / 本工程的类型等)
SYMBOL_STOPLIST = {
    "TEXT", "UE_LOG", "LogTemp", "UPROPERTY", "UFUNCTION", "UCLASS", "USTRUCT",
    "UENUM", "UINTERFACE", "GENERATED_BODY", "UMETA", "UInputComponent",
    "AI", "UI", "ID", "IO", "IE", "API",
}


def find_units(cfg: dict) -> list[dict]:
    input_dir: Path = cfg["paths"]["input"]
    units: list[dict] = []

    for header in sorted(input_dir.glob("*.h")):
        header_text = read_text(header)
        if not REFLECTION_RE.search(header_text):
            continue

        source = header.with_suffix(".cpp")
        source_text = read_text(source) if source.exists() else ""

        declared: list[str] = []
        for match in TYPE_DECL_RE.finditer(header_text):
            name = match.group(1) or match.group(2)
            if name and name not in declared:
                declared.append(name)

        files = [str(header.relative_to(ROOT))]
        if source.exists():
            files.append(str(source.relative_to(ROOT)))

        units.append(
            {
                "name": header.stem,
                "files": files,
                "declared_types": declared,
                "text": header_text + "\n" + source_text,
                "reflection_markers": len(REFLECTION_RE.findall(header_text)),
            }
        )

    return units


def strip_comments(text: str) -> str:
    return COMMENT_RE.sub(" ", text)


def collect_symbols(text: str, declared_types: list[str], known_types: set[str]) -> list[str]:
    """提取源码里需要查映射表的 UE 符号.

    declared_types: 本单元自己声明的类型(被翻译的对象, 不需要查表)
    known_types:    本批次其他单元声明的类型(属于同批迁移, 不算未覆盖)
    """
    found: set[str] = set()
    for regex in (UE_PREFIXED_RE, UE_MACRO_RE):
        for match in regex.finditer(strip_comments(text)):
            symbol = match.group(0)
            if symbol in SYMBOL_STOPLIST or len(symbol) < 3 or symbol.isdigit():
                continue
            if EXPORT_MACRO_RE.match(symbol):
                continue
            found.add(symbol)

    for name in list(declared_types) + sorted(known_types):
        found.discard(name)
    return sorted(found)


def match_mappings(text: str, entries: list[dict]) -> list[dict]:
    return [e for e in entries if e.get("ue") and e["ue"] in text]


def unmapped_symbols(symbols: list[str], entries: list[dict]) -> list[str]:
    needles = [e["ue"] for e in entries if e.get("ue")]
    result = []
    for symbol in symbols:
        if not any(needle in symbol or symbol in needle for needle in needles):
            result.append(symbol)
    return result


def cmd_scan(cfg: dict, args) -> int:
    entries, _ = load_mappings(cfg)
    units = find_units(cfg)
    if not units:
        log(f"在 {cfg['paths']['input']} 下没有找到含 UCLASS/USTRUCT/UENUM 的 UE 头文件")
        return 1

    # 本批次所有单元声明的类型: 互相引用属于"同批迁移", 不是未覆盖符号
    batch_types = {t for u in units for t in u["declared_types"]}

    report = []
    step(f"扫描到 {len(units)} 个翻译单元")
    for unit in units:
        symbols = collect_symbols(unit["text"], unit["declared_types"], batch_types)
        covered = match_mappings(unit["text"], entries)
        missing = unmapped_symbols(symbols, entries)

        covered_symbols = len(symbols) - len(missing)
        ratio = covered_symbols / max(len(symbols), 1)
        report.append(
            {
                "name": unit["name"],
                "files": unit["files"],
                "declared_types": unit["declared_types"],
                "reflection_markers": unit["reflection_markers"],
                "mapped_ids": [e["id"] for e in covered],
                "mapped_count": len(covered),
                "symbol_count": len(symbols),
                "symbol_coverage": round(ratio, 4),
                "unmapped": missing,
                "lines": unit["text"].count("\n") + 1,
            }
        )

        log(
            f"  {unit['name']:<22} 类型={','.join(unit['declared_types'])[:34]:<34} "
            f"行数={unit['text'].count(chr(10)) + 1:>4}  符号={len(symbols):>3}  "
            f"已覆盖={covered_symbols:>3}  未覆盖={len(missing):>3}  覆盖率={ratio:6.1%}"
        )

    write_json(cfg["paths"]["work"] / "units.json", report)
    log(f"\n结果写入 {cfg['paths']['work'] / 'units.json'}")

    all_missing = sorted({s for u in report for s in u["unmapped"]})
    if all_missing:
        log(f"\n全部未覆盖符号 ({len(all_missing)} 个) —— 这就是接下来要往映射表里补的东西:")
        log("  " + ", ".join(all_missing))
    return 0


# ---------------------------------------------------------------------------
# 阶段 2: plan —— 生成翻译任务包
# ---------------------------------------------------------------------------

def build_mapping_snippet(entries: list[dict], limit: int = 70) -> str:
    lines = []
    for entry in entries[:limit]:
        line = f"- `{entry['ue']}`  ->  `{entry['unity']}`   (置信度 {entry.get('confidence', 0)})"
        note = (entry.get("note") or "").strip()
        if note:
            line += f"\n    注意: {note}"
        lines.append(line)
    return "\n".join(lines) if lines else "(无)"


def cmd_plan(cfg: dict, args) -> int:
    entries, _ = load_mappings(cfg)
    units = find_units(cfg)
    template = read_text(cfg["paths"]["prompts"] / "unit_translate.md")
    task_dir = cfg["paths"]["work"] / "tasks"
    max_chars = cfg["translate"]["max_source_chars"]
    batch_types = {t for u in units for t in u["declared_types"]}

    step(f"生成 {len(units)} 个翻译任务包")
    for unit in units:
        symbols = collect_symbols(unit["text"], unit["declared_types"], batch_types)
        covered = match_mappings(unit["text"], entries)
        missing = unmapped_symbols(symbols, entries)

        source_text = unit["text"]
        truncated = len(source_text) > max_chars
        if truncated:
            source_text = source_text[:max_chars] + "\n// ...[源码过长已截断]"

        prompt = render(
            template,
            {
                "UNIT_NAME": unit["name"],
                "SOURCE_FILES": ", ".join(unit["files"]),
                "DECLARED_TYPES": ", ".join(unit["declared_types"]) or "(未识别)",
                "MAPPING_TABLE": build_mapping_snippet(covered),
                "UNMAPPED_SYMBOLS": ", ".join(missing) or "(无, 映射表已全覆盖)",
                "SOURCE_CODE": "```cpp\n" + source_text + "\n```",
                "FIX_SECTION": "",
            },
        )
        write_text(task_dir / f"{unit['name']}.prompt.md", prompt)
        write_json(
            task_dir / f"{unit['name']}.meta.json",
            {
                "unit": unit["name"],
                "files": unit["files"],
                "declared_types": unit["declared_types"],
                "mapped_ids": [e["id"] for e in covered],
                "unmapped": missing,
                "source_chars": len(unit["text"]),
                "truncated": truncated,
            },
        )

    log(f"任务包写入 {task_dir}")
    log("provider=api   时: 直接跑 `uv run migrate.py translate`")
    log("provider=agent 时: 把模型回答存成 work/tasks/<单元名>.resp.json 再跑 translate")
    return 0


# ---------------------------------------------------------------------------
# 阶段 3: translate —— 调用模型 / 读取人工响应
# ---------------------------------------------------------------------------

def extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError("模型输出里找不到合法 JSON")


def call_api(cfg: dict, system_prompt: str, user_prompt: str) -> dict:
    api = cfg["api"]
    env_name = api["api_key_env"]
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise RuntimeError(
            f"环境变量 {env_name} 未设置。请在当前终端执行 $env:{env_name} = \"sk-...\" 后重试; "
            "或把 config.yaml 的 provider 改回 agent"
        )

    body = {
        "model": api["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": api.get("temperature", 0.2),
        "max_tokens": api.get("max_tokens", 8192),
    }
    # 多数服务支持强制 JSON 输出; 少数(部分本地模型/代理)会直接返回 400,
    # 这时把 config.yaml 里的 json_mode 改成 false 即可, 解析端本来就有兜底。
    if api.get("json_mode", True):
        body["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        api["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=api.get("timeout", 300)) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"接口返回 {exc.code}: {detail}") from exc

    return extract_json(payload["choices"][0]["message"]["content"])


def write_unit_outputs(cfg: dict, unit_name: str, result: dict) -> list[str]:
    written = []
    out_dir: Path = cfg["paths"]["output"]
    for item in result.get("files", []):
        rel = item.get("path") or f"{unit_name}.cs"
        target = out_dir / Path(rel).name
        write_text(target, item.get("code", ""))
        written.append(str(target.relative_to(ROOT)))
    return written


def load_agent_response(task_dir: Path, unit_name: str) -> dict | None:
    """人工/智能体提供翻译结果的两种方式:

    1. work/tasks/<单元名>.resp.json          —— 结构化响应(api 与 agent 共用格式)
    2. work/tasks/<单元名>.files/*.cs         —— 直接放真实 .cs 文件, 可选配 <单元名>.notes.json

    方式 2 更适合人工评审: 编辑器里就有语法高亮和报错, 不用跟转义字符较劲.
    """
    response_path = task_dir / f"{unit_name}.resp.json"
    if response_path.exists():
        return read_json(response_path)

    files_dir = task_dir / f"{unit_name}.files"
    scripts = sorted(files_dir.glob("*.cs")) if files_dir.is_dir() else []
    if not scripts:
        return None

    notes_path = task_dir / f"{unit_name}.notes.json"
    meta = read_json(notes_path) if notes_path.exists() else {}
    return {
        "unit": unit_name,
        "confidence": meta.get("confidence", 0.5),
        "files": [{"path": p.name, "code": read_text(p)} for p in scripts],
        "notes": meta.get("notes", []),
        "todos": meta.get("todos", []),
        "unmapped": meta.get("unmapped", []),
    }


def cmd_translate(cfg: dict, args) -> int:
    task_dir = cfg["paths"]["work"] / "tasks"
    result_dir = cfg["paths"]["work"] / "results"
    provider = cfg["provider"]
    system_prompt = read_text(cfg["paths"]["prompts"] / "system_unity.md")

    tasks = sorted(task_dir.glob("*.prompt.md"))
    if getattr(args, "unit", None):
        tasks = [t for t in tasks if t.name.startswith(args.unit)]
    if not tasks:
        log("没有任务包, 先跑 `uv run migrate.py plan`")
        return 1

    step(f"翻译 {len(tasks)} 个单元 (provider={provider})")
    ok = failed = skipped = 0

    for task in tasks:
        unit_name = task.name[: -len(".prompt.md")]
        user_prompt = read_text(task)
        try:
            if provider == "api":
                result = call_api(cfg, system_prompt, user_prompt)
            else:
                result = load_agent_response(task_dir, unit_name)
                if result is None:
                    log(f"  跳过 {unit_name}: 缺少 {unit_name}.resp.json 或 {unit_name}.files/")
                    skipped += 1
                    continue
        except Exception as exc:  # noqa: BLE001 - 单个单元失败不应中断整批
            log(f"  失败 {unit_name}: {exc}")
            failed += 1
            continue

        result.setdefault("unit", unit_name)
        written = write_unit_outputs(cfg, unit_name, result)
        write_json(result_dir / f"{unit_name}.json", result)

        manual = [n for n in result.get("notes", []) if n.get("severity") == "manual"]
        conf = result.get("confidence", 0)
        log(
            f"  {unit_name:<22} 置信度={conf!s:<6} 文件={len(written)}  "
            f"提示={len(result.get('notes', []))}  待人工={len(manual)}"
        )
        ok += 1

    log(f"\n成功 {ok} / 失败 {failed} / 跳过 {skipped}")
    return 0 if failed == 0 else 2


# ---------------------------------------------------------------------------
# 阶段 4: verify —— 用真实 Unity 程序集做编译门禁
# ---------------------------------------------------------------------------

ERROR_RE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s*(?P<level>error|warning)\s+(?P<code>[A-Z]+\d+):\s*(?P<msg>.+)$"
)


def write_verify_project(cfg: dict) -> Path:
    managed = Path(cfg["paths"]["unity_managed"])
    references = "\n".join(
        f'    <Reference Include="{Path(m).stem}"><HintPath>{managed / m}</HintPath></Reference>'
        for m in cfg["verify"]["unity_modules"]
    )
    csproj = f"""<Project Sdk="Microsoft.NET.Sdk">
  <!-- 由 migrate.py 自动生成: 引用 Unity 2021.3 真实程序集做编译门禁 -->
  <PropertyGroup>
    <TargetFramework>{cfg['verify']['target_framework']}</TargetFramework>
    <EnableDefaultCompileItems>false</EnableDefaultCompileItems>
    <AssemblyName>MigratedScripts</AssemblyName>
    <GenerateAssemblyInfo>false</GenerateAssemblyInfo>
    <NoWarn>CS0169;CS0649;CS0414;CS0108;CS0067;CS0162;CS0219</NoWarn>
  </PropertyGroup>
  <ItemGroup>
{references}
  </ItemGroup>
  <ItemGroup>
    <Compile Include="..\\out\\UnityScripts\\**\\*.cs" />
  </ItemGroup>
</Project>
"""
    path = ROOT / "verify" / "CompileCheck.csproj"
    write_text(path, csproj)
    return path


def run_build(cfg: dict) -> dict:
    csproj = write_verify_project(cfg)
    env = dict(os.environ)
    env["DOTNET_CLI_UI_LANGUAGE"] = "en"  # 强制英文输出, 便于解析
    env["DOTNET_NOLOGO"] = "1"

    proc = subprocess.run(
        ["dotnet", "build", str(csproj), "--nologo", "-v", "q"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(ROOT),
    )
    output = (proc.stdout or "") + (proc.stderr or "")

    errors, warnings = [], []
    seen = set()  # MSBuild 会把同一条诊断同时写到 stdout 与 stderr, 这里去重
    for line in output.splitlines():
        match = ERROR_RE.match(line.strip())
        if not match:
            continue
        record = {
            "file": Path(match.group("file")).name,
            "line": int(match.group("line")),
            "col": int(match.group("col")),
            "code": match.group("code"),
            "message": match.group("msg").strip(),
        }
        # 同一条诊断在 msbuild 汇总行里会带项目路径后缀, 归一化后再判重
        record["message"] = re.sub(r"\s*\[[A-Za-z]:\\[^\]]*\]\s*$", "", record["message"])
        key = (record["file"], record["line"], record["col"], record["code"], record["message"])
        if key in seen:
            continue
        seen.add(key)
        (errors if match.group("level") == "error" else warnings).append(record)

    return {
        "success": proc.returncode == 0 and not errors,
        "exit_code": proc.returncode,
        "errors": errors,
        "warnings": warnings,
        "raw_tail": "\n".join(output.strip().splitlines()[-25:]),
    }


def cmd_verify(cfg: dict, args) -> int:
    scripts = sorted((cfg["paths"]["output"]).glob("*.cs"))
    step(f"编译门禁: {len(scripts)} 个 C# 文件  (引用 Unity 2021.3 真实程序集)")
    if not scripts:
        log("还没有生成任何 C# 文件, 先跑 translate")
        return 1

    started = time.time()
    result = run_build(cfg)
    elapsed = time.time() - started
    write_json(cfg["paths"]["work"] / "verify.json", result)

    if result["success"]:
        log(f"  编译通过 OK   用时 {elapsed:.1f}s   警告 {len(result['warnings'])} 条")
    else:
        log(f"  编译失败 FAIL 用时 {elapsed:.1f}s   错误 {len(result['errors'])} 条")
        for err in result["errors"][:25]:
            log(f"    {err['file']}({err['line']},{err['col']}) {err['code']}: {err['message']}")
    return 0 if result["success"] else 2


# ---------------------------------------------------------------------------
# 阶段 5: report —— 生成迁移报告
# ---------------------------------------------------------------------------

def cmd_report(cfg: dict, args) -> int:
    result_dir = cfg["paths"]["work"] / "results"
    verify_path = cfg["paths"]["work"] / "verify.json"
    verify = read_json(verify_path) if verify_path.exists() else None

    results = {}
    for path in sorted(result_dir.glob("*.json")):
        data = read_json(path)
        results[data.get("unit", path.stem)] = data

    if not results:
        log("还没有任何翻译结果, 先跑 translate")
        return 1

    errors_by_file: dict[str, list[dict]] = {}
    for err in (verify or {}).get("errors", []):
        errors_by_file.setdefault(err["file"], []).append(err)

    scripts = sorted((cfg["paths"]["output"]).glob("*.cs"))
    total_manual = 0

    head = [
        "# UE -> Unity 迁移报告",
        "",
        f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 源目录: `{cfg['paths']['input'].relative_to(ROOT)}`",
        "- 目标: Unity 2021.3 / C# / 旧版 Input Manager / CharacterController",
        f"- 编译门禁: {'通过' if (verify or {}).get('success') else ('未通过' if verify else '未运行')}",
        f"- 生成脚本: {len(scripts)} 个",
        "",
        "## 单元总览",
        "",
        "| 单元 | 输出文件 | 置信度 | 编译 | 提示 | 待人工 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for unit_name, data in results.items():
        files = [f["path"] for f in data.get("files", [])]
        notes = data.get("notes", [])
        manual = [n for n in notes if n.get("severity") == "manual"]
        total_manual += len(manual)

        file_errors = sum(len(errors_by_file.get(Path(f).name, [])) for f in files)
        if not verify:
            status = "未验证"
        else:
            status = "通过" if file_errors == 0 and verify.get("success") else f"失败 {file_errors}"

        conf = data.get("confidence", 0)
        conf_text = f"{conf:.2f}" if isinstance(conf, (int, float)) else str(conf)
        head.append(
            f"| `{unit_name}` | {', '.join('`' + f + '`' for f in files)} | {conf_text} | "
            f"{status} | {len(notes)} | {len(manual)} |"
        )

    body = list(head)

    if verify and not verify["success"]:
        body += ["", "## 编译错误", ""]
        for err in verify["errors"][:40]:
            body.append(f"- `{err['file']}({err['line']},{err['col']})` **{err['code']}** {err['message']}")

    manual_items = [
        (unit, note)
        for unit, data in results.items()
        for note in data.get("notes", [])
        if note.get("severity") == "manual"
    ]
    if manual_items:
        body += ["", "## 必须人工处理", ""]
        body += [f"- **{unit}**: {note['text']}" for unit, note in manual_items]

    todo_items = [(unit, todo) for unit, data in results.items() for todo in data.get("todos", [])]
    if todo_items:
        body += ["", "## Unity 编辑器内的收尾步骤", ""]
        body += [f"- **{unit}**: {todo}" for unit, todo in todo_items]

    unmapped_all = sorted({s for data in results.values() for s in data.get("unmapped", [])})
    if unmapped_all:
        body += ["", "## 模型自报未覆盖的符号", "", ", ".join(f"`{s}`" for s in unmapped_all)]

    body += [
        "",
        "## 下一步",
        "",
        "1. 在 Unity 里新建工程, 把 `out/UnityScripts/` 下的脚本拖进去.",
        "2. 按上表 `待人工` 项配置: Layer / 碰撞层 / Inspector 引用 / 按键映射.",
        "3. 场景搭好后做差分测试(同一组输入比较 UE 与 Unity 的行为)验证手感一致性.",
        "",
    ]

    path = cfg["paths"]["reports"] / "migration_report.md"
    write_text(path, "\n".join(body))
    log(f"\n报告写入 {path}")
    log(f"待人工处理项: {total_manual}")
    return 0


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------

def cmd_doctor(cfg: dict, args) -> int:
    """跑之前先自检: 路径、映射表、Unity 程序集、dotnet、以及 API 连通性。"""
    problems: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> bool:
        log(f"  [{'OK  ' if condition else 'FAIL'}] {label}" + (f"   -> {detail}" if detail else ""))
        if not condition:
            problems.append(label)
        return condition

    step("环境自检")

    for key in ("input", "mappings", "prompts"):
        path = cfg["paths"][key]
        check(f"路径 {key}", path.exists(), str(path))

    try:
        entries, _ = load_mappings(cfg)
        check("映射表可解析", len(entries) > 0, f"{len(entries)} 条条目")
    except Exception as exc:  # noqa: BLE001
        check("映射表可解析", False, str(exc)[:200])

    units = find_units(cfg)
    unit_names = ", ".join(u["name"] for u in units)
    check("input 中有可翻译单元", len(units) > 0, f"{len(units)} 个: {unit_names}" if units else "检查 .h 里是否有 UCLASS/USTRUCT/UENUM 标记, 且 .h 与 .cpp 同名配对")

    managed = Path(cfg["paths"]["unity_managed"])
    if check("Unity 程序集目录存在", managed.is_dir(), str(managed)):
        missing = [m for m in cfg["verify"]["unity_modules"] if not (managed / m).exists()]
        check(
            "编译门禁所需程序集齐全",
            not missing,
            ("缺失: " + ", ".join(missing)) if missing else f"{len(cfg['verify']['unity_modules'])} 个都在",
        )

    try:
        proc = subprocess.run(
            ["dotnet", "--version"], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        check("dotnet 可用", proc.returncode == 0, proc.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        check("dotnet 可用", False, "找不到 dotnet 命令")

    provider = cfg["provider"]
    check("provider 取值合法", provider in ("api", "agent"), provider)

    if provider == "api":
        api = cfg["api"]
        key_name = api["api_key_env"]
        key = os.environ.get(key_name, "").strip()
        check(
            f"环境变量 {key_name} 已设置",
            bool(key),
            f"长度 {len(key)}"
            if key
            else f'请在当前终端执行 $env:{key_name} = "sk-..."',
        )
        check("base_url 格式", str(api["base_url"]).startswith("http"), api["base_url"])
        if key:
            log(f"  正在向 {api['base_url']} 发一次最小请求, 验证接口连通与 JSON 模式 ...")
            try:
                probe = call_api(cfg, "你是连通性测试助手。", '只输出这个 JSON: {"ok": true}')
                check("接口连通且返回可解析 JSON", isinstance(probe, dict), str(probe)[:120])
            except Exception as exc:  # noqa: BLE001
                check("接口连通且返回可解析 JSON", False, str(exc)[:300])
    else:
        log("  [INFO] provider=agent: 不需要 API Key;")
        log("         结果来自 work/tasks/<单元>.resp.json 或 work/tasks/<单元>.files/*.cs")

    log("")
    if problems:
        log(f"自检未通过: {len(problems)} 项待处理 -> {', '.join(problems)}")
        return 2
    log("自检全部通过, 可以跑 `uv run migrate.py all`")
    return 0


def cmd_all(cfg: dict, args) -> int:
    codes = []
    for name, func in (
        ("scan", cmd_scan),
        ("plan", cmd_plan),
        ("translate", cmd_translate),
        ("verify", cmd_verify),
    ):
        code = func(cfg, args)
        codes.append((name, code))
        if code == 1:
            break
    cmd_report(cfg, args)

    log("\n=== 流程汇总 ===")
    for name, code in codes:
        log(f"  {name:<10} {'OK' if code == 0 else ('待补' if code == 1 else 'FAIL')}")
    return 0 if all(code == 0 for _, code in codes) else 2


def main() -> int:
    parser = argparse.ArgumentParser(prog="migrate.py", description="UE -> Unity 代码迁移工具")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("doctor", "环境自检: 路径 / 映射表 / Unity 程序集 / API 连通性"),
        ("scan", "扫描 UE 源码, 输出翻译单元与映射覆盖率"),
        ("plan", "生成翻译任务包"),
        ("translate", "调用模型或读取人工响应完成翻译"),
        ("verify", "用真实 Unity 程序集编译生成的 C#"),
        ("report", "生成迁移报告"),
        ("all", "全流程"),
    ):
        sub.add_parser(name, help=help_text).add_argument("--unit", help="只处理指定单元(前缀匹配)")

    args = parser.parse_args()
    cfg = load_config()
    handlers = {
        "doctor": cmd_doctor,
        "scan": cmd_scan,
        "plan": cmd_plan,
        "translate": cmd_translate,
        "verify": cmd_verify,
        "report": cmd_report,
        "all": cmd_all,
    }
    return handlers[args.command](cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
