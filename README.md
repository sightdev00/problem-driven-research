# Problem Driven Research · 架构草案

A resumable, problem-driven research workflow for evidence-aware literature discovery, domain protocols, source registries, and human-reviewed conclusions.

此压缩包包含架构讨论稿与**首轮研究的可运行入口**。首轮流程能完成问题框架确认、公开资料检索和阶段性分析；实验执行及持续轮次仍待工程仓库和真实主题验证。

## 一键查看

解压后在目录内运行：

```bash
./run.sh
```

Windows PowerShell：

```powershell
.\run.ps1
```

脚本检查草案文件，打印阅读顺序，然后询问是否启动研究。阅读完毕后也可直接执行 `./run.sh --start`（PowerShell：`.\run.ps1 -Start`），或者 `./research.sh`（PowerShell：`.\research.ps1`）。`--check` / `-Check` 只执行检查。

## 接入已有 Qwen 服务

研究脚本使用 Python 3 标准库，无需安装新的模型服务。请在启动前设置你**已有**的兼容 OpenAI API 的服务地址和模型名；以下仅为示例，需换成服务器上的真实值：

```bash
export QWEN_BASE_URL='http://127.0.0.1:8000/v1'
export QWEN_MODEL='Qwen/Qwen3.8-27B'
export QWEN_API_KEY='已有服务要求的令牌'  # 无鉴权时省略
./run.sh --start
```

如果从另一台机器访问服务器，请使用实际可达的地址。脚本不会把令牌写入研究记录。Windows PowerShell 用 `$env:QWEN_BASE_URL = 'http://.../v1'`、`$env:QWEN_MODEL = '...'` 设置变量；有鉴权时再设置 `$env:QWEN_API_KEY`。

流程：录入原始现象和目标 → 模型提出可判断的研究问题及 2–3 个互补检索词 → 人确认问题框架（物理领域必须明确具体分支 scope 和进展判据 progress_criterion；研究综述无须强造竞争因果假设）→ 检查题目补充网址（必查，但不是系统检索源）→ 按全局 [来源注册表](structure/sources.json) 读取领域机构页面，并查询 OpenAlex（单次 10 条，避免限流）与 Crossref（每个检索词各 10 条）；物理理论另查询 arXiv（主检索词 10 条预印本）→ 去重后读取至多五篇发现论文的公开落地页 → 模型分析来源 → 人确认阶段性资料线索。每个研究保存于 `research/<时间戳-编号>/`；中断后运行 `./research.sh resume`。需要补充同一研究时运行 `./research.sh refresh`：它会先将当前 `sources.json`、`analysis.json` 与已有报告归档到 `research/<编号>/rounds/<时间戳>/`，再重新检索和分析。已有研究的问题定义不合格时运行 `./research.sh reframe`：先归档现有分析，修改该研究的 `draft.json` 中的 `question`、`scope` 和 `progress_criterion`，然后 `./research.sh resume` 确认并重新检索。人工网址读取仅支持公开的 HTML 或纯文本；PDF 暂需提供人工提炼的原文材料。未读到的网页和访问验证页不能作证据；仅有论文摘要时，报告明确标注待全文核验。

资料较多时，分析会按五条来源为一组逐批审查，完成的批次保存在 `analysis_chunks.json`；中断后 `resume` 会从已保存的批次继续，再进行最终综合。

**当前限制：**实验脚本生成尚需真实工程仓库的实验能力说明；当前首轮不会自动执行实验，也不支持自动无限轮研究。客户原始素材不要填入公开网址或上传到模型服务；模型只接收本轮问题文本及公开来源摘录。

## 阅读顺序

1. [目标与八项原则](docs/PRINCIPLES.md)
2. [架构总览](docs/ARCHITECTURE.md)
3. [架构决定与未定事项](docs/DECISIONS.md)
4. [DMS 验证主题推演](research/dms-eye-context/WALKTHROUGH.md)

内置领域协议为 `visual-intelligence` 与 `physics`；每项研究会锁定其领域版本。`structure/` 展示领域协议、来源注册表、实验能力说明的样例，尤其**示例实验命令不可运行**。当前真实研究数据默认不纳入 Git；发布仓库前仍应人工审查 `research/` 内容。

要删除一项研究，运行 `./research.sh delete`，选择编号后输入精确的 `DELETE`。研究目录会移到 `research/.trash/`，不会直接永久删除。
