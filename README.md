# CodeUEToUnity

基于 LLM 的 Unreal Engine C++ 到 Unity C# 代码迁移工具。

输入 UE 源码，输出可直接编译的 Unity C# 脚本，并生成一份标明「必须人工处理」事项的迁移报告。
当前覆盖角色控制器：位移、跳跃、爬墙、弹簧臂相机。

## 特性

- **阶段化流水线**：`scan` → `plan` → `translate` → `verify` → `report` 五个独立阶段，中间产物全部落盘，任一阶段可单独重跑。
- **映射表驱动**：内置 112 条 UE→Unity 符号映射，每条附带语义差异说明，包括单位换算、坐标系差异、生命周期对应关系，以及没有对应物时的替代方案。
- **编译门禁**：引用本机 Unity 安装目录下的引擎程序集做编译校验，不依赖 Unity 编辑器，也不需要激活许可证，单次校验约 1 秒。
- **双工作模式**：可直连任意 OpenAI 兼容接口自动翻译，也可由人工或外部模型产出结果后回填。两种模式共用同一套任务包。
- **迁移报告**：汇总每个翻译单元的自评置信度与编译状态，并把必须人工处理的项单独列出。
- **环境自检**：`doctor` 命令一次性检查路径、映射表、引擎程序集、编译链与接口连通性。

## 工作原理

| 阶段 | 职责 | 输出 |
| --- | --- | --- |
| `scan` | 定位翻译单元，剥离注释后提取 UE 符号，与映射表比对 | `work/units.json`，含映射覆盖率与未覆盖符号清单 |
| `plan` | 组装翻译任务包：系统提示 + 本单元命中的映射条目 + 源码 | `work/tasks/<单元>.prompt.md` |
| `translate` | 调用模型或读取人工回填的产物，归一化为统一结构 | `work/results/<单元>.json`、`out/UnityScripts/*.cs` |
| `verify` | 编译生成的 C#，解析结构化诊断信息 | `work/verify.json` |
| `report` | 汇总上述结果，生成迁移报告 | `reports/migration_report.md` |

`scan` 输出的未覆盖符号清单是本项目的扩展入口：它指出映射表尚未覆盖的 UE 符号，
补充后覆盖率随之上升，翻译过程中的不确定性相应减少。

## 环境要求

| 依赖 | 说明 |
| --- | --- |
| Windows + PowerShell | 当前仅在 Windows 上验证 |
| Python 3.10+ | 由 `uv` 管理，无需手动创建虚拟环境 |
| .NET SDK | 编译门禁使用，本项目在 9.0.316 上验证 |
| Unity 2021.3 | 仅需 `Editor/Data/Managed/UnityEngine` 目录下的引擎程序集 |

## 快速开始

```powershell
# 环境自检
uv run migrate.py doctor

# 全流程
uv run migrate.py all
```

`uv` 会在首次运行时自动安装 Python 依赖。

### 分步执行

```powershell
uv run migrate.py scan        # 扫描源码，输出覆盖率与未覆盖符号
uv run migrate.py plan        # 生成翻译任务包
uv run migrate.py translate   # 翻译
uv run migrate.py verify      # 编译门禁
uv run migrate.py report      # 生成迁移报告
```

## 命令参考

| 命令 | 说明 |
| --- | --- |
| `doctor` | 环境自检：路径、映射表、引擎程序集、编译链、接口连通性 |
| `scan` | 扫描 `input/` 下的 UE 源码，统计映射覆盖率 |
| `plan` | 生成翻译任务包 |
| `translate` | 完成翻译，`--unit <前缀>` 可只处理指定单元 |
| `verify` | 用真实 Unity 程序集编译 `out/UnityScripts/` |
| `report` | 生成迁移报告 |
| `all` | 依次执行完整流程 |

## 配置

配置文件为 `config.yaml`，分为 `paths`、`provider`、`api`、`translate`、`verify` 五段。

### 工作模式

`provider` 有两个取值：

- `agent`：翻译结果由人工或外部模型产出，回填到 `work/tasks/<单元>.files/` 后执行 `translate`。
- `api`：直连 OpenAI 兼容接口完成翻译。

切换到 `api` 模式需要配置 `api` 段并设置 API 密钥：

```yaml
provider: api
api:
  base_url: "https://api.deepseek.com/v1"
  model: "deepseek-chat"
  api_key_env: "DEEPSEEK_API_KEY"
```

`api_key_env` 填写的是环境变量的名称，密钥本身不写入配置文件。

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
```

需要永久生效时：

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "sk-...", "User")
```

除 DeepSeek 外，通义千问、智谱、Kimi 以及本机的 vLLM、Ollama 均可使用，
前提是接口兼容 OpenAI 的 `/chat/completions`。若服务不支持 `response_format`，
把 `api.json_mode` 改为 `false`。

### 引擎程序集路径

`paths.unity_managed` 指向 Unity 的引擎程序集目录，更换机器或 Unity 版本时需要修改：

```yaml
paths:
  unity_managed: "C:/Program Files/Unity/Hub/Editor/2021.3.45f1c1/Editor/Data/Managed/UnityEngine"
```

`verify.unity_modules` 列出编译门禁需要引用的程序集。若生成代码用到了新的 Unity 模块，
把对应的 `UnityEngine.*Module.dll` 加入该列表即可，无需改动代码。

## 输入约定

- 只识别 `.h` 后缀的头文件，并按同名查找 `.cpp`。
- 头文件中必须出现 `UCLASS(`、`USTRUCT(`、`UENUM(` 或 `UINTERFACE(` 之一，否则该文件会被跳过。
- 翻译单元的名称取自文件名，而非类名。
- `.cpp` 缺失时仍可处理，但翻译质量会下降。

## 目录结构

```
migrate.py                      命令行入口，五个阶段与自检
config.yaml                     路径、工作模式、接口与编译参数
mappings/ue2unity.yaml          UE→Unity 符号映射表
prompts/system_unity.md         目标侧架构约定与语义陷阱清单
prompts/unit_translate.md       单单元任务包模板
input/                          UE 源码，同名 .h/.cpp 配对为一个翻译单元
out/UnityScripts/               生成的 Unity C# 脚本
verify/CompileCheck.csproj      编译门禁工程（自动生成）
reports/migration_report.md     迁移报告（自动生成）
work/                           中间产物（自动生成）
docs/                           开发文档
```

各目录与文件的详细说明见 [docs/手写指南.md](docs/手写指南.md)。

## 扩展方式

**补充映射知识**

在 `mappings/ue2unity.yaml` 的 `entries` 下追加条目：

```yaml
  - id: x-my-symbol
    category: character
    ue: FMyThing
    unity: MyThingClass
    confidence: 0.9
    note: "单位差异、语义陷阱，或无对应物时的替代方案"
```

`ue` 字段采用子串匹配。单个单元命中的条目超过 70 条时会按文件顺序截断，
常用条目应放在文件靠前的位置。

**调整目标架构**

`prompts/system_unity.md` 的内容会作为 system message 原样发送给模型。
若目标侧改用新版输入系统、Cinemachine 或自定义框架基类，在此处调整。

**扩大迁移范围**

把新的 `.h`、`.cpp` 放入 `input/`，重新执行 `scan`，即可看到映射表尚未覆盖的符号。

## 已知限制

- **不支持 Blueprint**：`.uasset` 为二进制图结构，需要先由 UE 编辑器导出图数据。
- **无跨文件依赖分析**：一个 `.h`、`.cpp` 对即一个翻译单元，不构建调用图。
- **仅有编译校验，无行为校验**：通过编译不代表行为等价，数值与手感一致性仍需人工验证。
- **无增量缓存**：源码变更后整批重跑。
- **无自动修复闭环**：`config.yaml` 中的 `max_fix_rounds` 尚未接入，编译报错不会自动回灌给模型。

## 技术栈

- Python 3.12，依赖由 `uv` 管理，使用 PyYAML
- .NET SDK 9 与 MSBuild，编译门禁引用 Unity 2021.3 引擎程序集
- OpenAI 兼容 Chat Completions 接口，`response_format: json_object`
