# UE -> Unity 代码迁移工具 (MVP v0.1)

把一个 UE C++ 角色类的**移动 / 跳跃 / 爬墙 / 相机**逻辑, 自动翻译成能编译通过的 Unity C# 脚本。

当前版本刻意做窄: 只覆盖角色控制器这一类代码, 但管道是通用的 —— 引擎抽象层、验证闭环、
映射知识库都在, 以后往里加 Actor/Component/委托/Timer/状态机只是扩展映射表和提示词,
不需要改架构。

> **想自己动手改这个工具, 先看 [docs/手写指南.md](docs/手写指南.md)** ——
> 逐个文件夹、逐个文件讲清楚哪些是手写的、哪些是自动生成的、语法规则是什么、常踩的坑有哪些。

## 30 秒上手

```powershell
cd E:\XYProjects\UEToUnity

# 0. 环境自检: 路径 / 映射表 / Unity 程序集 / API 连通性, 一次把问题找出来
uv run migrate.py doctor

# 看扫描结果: 找出翻译单元, 统计映射覆盖率, 列出还没覆盖的 UE 符号
uv run migrate.py scan

# 生成每个单元发给模型的完整任务包
uv run migrate.py plan

# 翻译: provider=agent 时读取人工/智能体写好的结果
uv run migrate.py translate

# 编译门禁: 用 Unity 2021.3 真实程序集编译生成的 C#, 1~2 秒出结果
uv run migrate.py verify

# 生成迁移报告(含必须人工处理的清单)
uv run migrate.py report

# 或者一条命令全跑
uv run migrate.py all
```

首次运行 `uv` 会自动装好 Python 依赖, 不需要手工建虚拟环境。

## 目录结构

```
migrate.py                     五个阶段的 CLI(单文件)
config.yaml                    路径 / LLM 提供方 / 编译参数
mappings/ue2unity.yaml         ★ UE->Unity 符号映射表, 工具的"知识"
prompts/system_unity.md        ★ 目标侧架构约定和语义陷阱清单
prompts/unit_translate.md      单单元翻译模板
input/                         放 UE C++ 源码(.h 与同名 .cpp 配对即一个翻译单元)
work/                          中间产物
  units.json                     扫描结果与覆盖率
  tasks/<单元>.prompt.md         发给模型的完整任务包
  tasks/<单元>.files/*.cs        agent 模式下人工/智能体提供的 C#
  tasks/<单元>.notes.json        置信度/提示/待办
  results/<单元>.json            归一化后的翻译结果
  verify.json                    编译诊断
out/UnityScripts/              产出的 Unity C# 脚本
verify/CompileCheck.csproj     自动生成的编译门禁工程
reports/migration_report.md    迁移报告
```

## 五个阶段

| 阶段 | 做什么 | 关键点 |
| --- | --- | --- |
| `scan` | 找出翻译单元, 匹配映射表, 剥离注释后提取 UE 符号 | 输出"未覆盖符号清单" —— 这是工具的自我进化入口 |
| `plan` | 把系统提示 + 相关映射条目 + 源码拼成任务包 | 只把**本单元真正用到的**映射条目喂给模型, 省 token 又减少干扰 |
| `translate` | `api` 直连模型 / `agent` 读人工产物 | 两种 provider 共用同一套任务包, 可随时切换 |
| `verify` | 引用真实 `UnityEngine.*.dll` 编译生成的脚本 | 不要许可证、不要 Unity 编辑器, 1~2 秒出结果 |
| `report` | 汇总置信度、编译状态、必须人工处理的项 | 明确列出"工具做不了、必须人来"的部分 |

## 编译门禁为什么这么做

`verify/CompileCheck.csproj` 直接引用本机 Unity 安装目录下的真实程序集:

```
C:\Program Files\Unity\Hub\Editor\2021.3.45f1c1\Editor\Data\Managed\UnityEngine\*.dll
```

比手写 `UnityEngine` 桩代码准得多(桩只能校验你想到的那部分 API), 又比跑 Unity batchmode 快得多
(不用激活许可证, 不用起编辑器)。如果你的 Unity 版本或安装路径不同, 改 `config.yaml` 里的
`paths.unity_managed` 即可。

## 换成真正的 LLM 自动翻译

改 `config.yaml` 的两处:

```yaml
provider: api                                # 原来是 agent
api:
  base_url: "https://api.deepseek.com/v1"   # 任何 OpenAI 兼容接口都行
  model: "deepseek-chat"
  api_key_env: "DEEPSEEK_API_KEY"           # 只写"变量名"; 密钥走环境变量, 不落盘
```

密钥只从环境变量读, **不写进任何配置文件** —— 这样 `config.yaml` 可以放心提交和分享。

在 PowerShell 里设置(只在当前窗口有效):

```powershell
$env:DEEPSEEK_API_KEY = "sk-你的密钥"
```

想永久生效(设置后需要重开终端):

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "sk-你的密钥", "User")
```

设完先自检, 确认接口通、JSON 模式可用, 再跑全流程:

```powershell
uv run migrate.py doctor
uv run migrate.py all
```

另外 `provider` 也可以用环境变量临时覆盖, 方便在两种模式之间切换, 不必反复改配置:

```powershell
$env:MIGRATE_PROVIDER = "api"     # 或 "agent"
```

也支持通义千问(百炼)、智谱、Kimi、以及本机 vLLM/Ollama —— 只要接口兼容 OpenAI 的
`/chat/completions` 并且支持 `response_format: json_object`。代码不出内网就指向本机模型。

## 怎么扩展(这是设计里的重点)

**加映射知识** —— 直接往 `mappings/ue2unity.yaml` 的 `entries` 里加一组:

```yaml
  - id: x-my-symbol
    category: character
    ue: FMyThing
    unity: MyThingClass
    confidence: 0.9
    note: "语义差异写在 note 里, 这一列才是真正值钱的部分"
```

`scan` 报出什么未覆盖符号, 就往这里补什么, 补完覆盖率上升、模型幻觉下降。

**加迁移目标** —— 编辑 `prompts/system_unity.md`, 写入你的目标架构约定。
想让输出走新输入系统、Cinemachine、或者你自己的框架基类, 都在这里定。

**加翻译范围** —— 把新的 UE `.h/.cpp` 丢进 `input/`, 重跑即可。
`scan` 会告诉你映射表还缺哪些符号。

**加一个真实反馈回路** —— 每次人工修正过的产物, 就是最好的 few-shot 样本。
把 `out/UnityScripts/` 里被接受的版本收集进一份 `mappings/accepted_pairs/`,
在 `plan` 阶段按相似度检索注入任务包, 这是准确率提升最快的一步。

## 当前能力边界(诚实的部分)

已经做到的:

- 角色移动 / 跳跃 / 爬墙 / 弹簧臂相机这套逻辑的迁移, 产物**编译通过**。
- 自动识别并报告"必须人工处理"的项(碰撞层、按键映射、Layer、轴值域差异)。
- 单位换算(厘米->米)、Z-up -> Y-up、Tick -> Update、无重力 -> 自写重力等语义改造。

还没做的:

- **Blueprint**: `.uasset` 是二进制图结构, 必须先让 UE 编辑器导出图 JSON。这是最大的缺口。
- **跨文件依赖分析**: 目前一个 `.h/.cpp` 对就是一个单元, 不做调用图。
- **行为等价验证**: 只有编译门禁, 没有差分测试。手感一致性仍需人工在编辑器里调。
- **增量缓存**: 改了源码会整批重跑, 还没有按内容哈希跳过。
- **自动修复循环**: `config.yaml` 里有 `max_fix_rounds`, 但回到 `translate` 的闭环还没接上。

## 建议的下一步

1. 把你真实的 UE 角色类丢进 `input/`, 跑 `scan` 看映射表缺什么, 补齐后再 `plan`。
2. 配一个 API Key, 走通"模型直译 + 编译门禁"的自动路径。
3. 拿一个真实关卡在 UE 和 Unity 里用同一组输入跑, 比对位置曲线, 这才是行为正确性的证据。
4. 再往上加 Blueprint 导出和增量缓存。
