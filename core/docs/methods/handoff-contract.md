# Research handoff contract（研究交接契约）

这是一份面向研究交接的最小、确定性一致性检查。它沿用已有的
`SplitPlan`、`Run` 和 `Artifact` 对象，核对下游步骤声明消费了什么、产出了
什么；它不新增 runtime，也不替代这些对象。

检查入口是
[`scripts/verification/check_research_handoff.py`](../../scripts/verification/check_research_handoff.py)
命令为：

```text
python scripts/verification/check_research_handoff.py --manifest PATH [--out PATH]
```

输入 JSON 的 `schema_version` 必须是 `"research-handoff-v1"`，并包含四组对象：
`splits`、`evaluation`、`weights` 和 `execution`。额外字段会被忽略，因此可以
同时保留既有 `Run`、`Artifact`、`SplitPlan` 的 ID 和链接。

`--out` 如果解析后与 `--manifest` 指向同一文件（包括相对路径别名），检查器
会拒绝写入并返回 JSON 错误，避免报告覆盖输入记录。

## 输出与状态

报告包含 `checks` 列表。每项都有稳定的 `id`、`status` 和可读的 `reason`；可选
的 `details` 字段用于定位 ID、缺失项和计数。报告还明确声明：

```json
{
  "evidence_scope": "provided_manifest_only",
  "scientific_acceptance": "not_assessed"
}
```

这两个字段不能被省略其含义：`pass` 只表示输入记录满足本契约的一致性规则，
不证明运行真的发生、文件中确实有相应内容，也不证明模型在生物科学上正确。

状态含义如下：

- `pass`：根据这份记录，所检查的一致性规则成立。
- `fail`：记录含有明确矛盾，或明确缺少要求完成的动作。
- `unknown`：必要证据没有提供，无法作出判断。
- `not_applicable`：记录明确表明该条件不适用；它不能把缺失的评价分母变成通过。

`overall` 按 `fail` 优先、再 `unknown`、最后 `pass` 汇总。CLI 退出码为
`pass=0`、`fail=1`、`unknown=2`。无效 JSON、字段类型错误、文件不存在或 schema 不支持时也退出
`2`，并在可解析的 JSON 中给出清晰的顶层 `error`，不要求用户从 traceback 中
猜原因。

## Minimum record

### `splits`

```json
{
  "independence_required": true,
  "train_ids": ["..."],
  "validation_ids": ["..."],
  "test_ids": ["..."],
  "rationale": "Why this split policy answers the research question"
}
```

三组 ID 必须是非空字符串，并且每组内部唯一。`independence_required` 为
`true` 时，三组之间也必须两两互斥。为 `false` 时允许重叠：检查结果使用
`not_applicable`，保留三组的两两重叠计数，并把这种传导式或其他非互斥设计留给
Review；此时必须提供非空 `rationale`。缺少 split 证据时为 `unknown`。

这里仅检查声明的 ID 和政策，不判断同源、供体、时间或其他领域划分是否适合该
生物学问题。

### `evaluation`

```json
{
  "required_ids": ["evaluation-input-1"],
  "predicted_ids": ["evaluation-input-1"],
  "failed_ids": [],
  "excluded": []
}
```

`predicted_ids`、`failed_ids` 和 `excluded[].id` 是 `required_ids` 中每个 ID 的
三种互斥出口。它们不能重复、不能包含未知 ID，也不能漏掉必需 ID。每项
`excluded` 必须有非空文字 `reason`；这只证明记录了说明，不证明排除合法或对
科学结论无害。

只要有 `failed_ids`，评价检查就失败，即使所有输入都已对账。若含排除项但三种
出口完整对账，`evaluation.accounting` 可以通过；`evaluation.coverage` 仍为
`unknown`，直到 Review 判断排除是否保留了目标科学覆盖。空的 `required_ids`
也只能是 `unknown`，因为它没有评价分母。

此检查器不推断 `evaluation` 与 `splits` 之间的角色绑定，也不推断多个评价集
之间的身份对应；调用者必须把这些关系保存在相应的 `Run`、`Artifact` 或
`SplitPlan` 记录中并交给 Review。

### `weights`

```json
{
  "pretraining_required": true,
  "expected_revision": "revision-id-or-null",
  "loaded_revision": "revision-id-or-null",
  "loaded": true,
  "unexpected_missing_keys": [],
  "intentional_skips": []
}
```

要求预训练时，`loaded` 必须为 `true`。如果声明了两个 revision，它们必须一致；
用于比较的值缺失时为 `unknown`，不一致时为 `fail`。非空
`unexpected_missing_keys` 始终失败。`intentional_skips` 可以记录
`{ "key": "...", "reason": "..." }`，但不能替意外缺失的参数键开脱。若明确
不要求预训练且 `loaded` 为 `false`，权重加载为 `not_applicable`。当
`pretraining_required` 为 `true` 时，显式的 `"expected_revision": null` 表示
必需权重身份没有定义，状态是 `unknown`；只有明确的 optional/scratch 情形才
把空 revision 判为 `not_applicable`。

检查器不会打开 checkpoint，也不会加载模型参数。revision 相同只说明元数据
一致，不说明张量内容一致。

### `execution`

```json
{
  "required_step_ids": ["step-load", "step-evaluate"],
  "completed_step_ids": ["step-load", "step-evaluate"]
}
```

必需步骤必须唯一，并且都出现在已完成列表中。重复会被清楚列出并使步骤检查
失败。额外的 completed ID 会在 `execution.extra_steps` 中作为辅助步骤、并标出
其未列入必需集合；它们本身不导致失败。缺少步骤证据为 `unknown`。空的
`required_step_ids` 也只能是 `unknown`，不能由空集合制造“执行成功”的声明。

## 范围与 Review 边界

这是对声明记录的确定性一致性检查。它不证明执行发生，不训练或推理，不检查
预测文件，不验证参数张量，不重算指标，不做超出所列 ID 的泄漏分析，也没有
生物科学正确性认证。`excluded` 是否合理、划分是否适合研究问题、结果是否支持
某个主张，仍由 Review 判断；人的判断不能由本程序自动生成。因而报告 `pass`
不能直接当作科学验收记录。

可直接运行的纯虚构示例是
[`examples/verification/research-handoff.json`](../../examples/verification/research-handoff.json).
它没有真实训练或推理记录。

## 案例经验如何进入交接记录

这份交接表把五类常见缺口变成可执行的记录项：

- **DNABERT-2**：保留有效参数、模型选择、关键产物和指标覆盖，交接时说明每项对应的运行与评价阶段。
- **RNA-FM**：按 split 保存样本 ID、预测、真值、checkpoint 身份和指标覆盖，便于复核分母与输出。
- **ESM2/ESMFold**：逐输入记录推理状态，并核对失败、缺失和已产出的结果。
- **MolCLR**：记录组件/权重在哪个阶段实际被消费，并把评价输入与结果产物对应起来。
- **scGPT**：把输入身份、配置和结果覆盖放在同一交接中，便于定位版本与范围。

公开论文及方法背景可从[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)进入。这里的检查项是项目对案例经验的工程整理；案例原始定位和细节保存在[机器记录](../../../machine/document-notes/core/docs/methods/handoff-contract.md.json)，旧归档指针也集中在[机器索引](../../../machine/archive-pointers.jsonl)。
## 2026-10-14：关系检查的独立扩展

[research-relations-v1](relations-contract.md)补充角色/阶段到工件、选择事件到评价角色、标量线性聚合贡献的检查。旧research-handoff-v1的schema和行为保持不变；其额外字段仍不属于自动核验范围。两个检查器均只判断提交记录，不捕获真实运行或认证科学结论。设计演进见[D24–D26](../design/13-calibrated-relations-and-checks.md)。
