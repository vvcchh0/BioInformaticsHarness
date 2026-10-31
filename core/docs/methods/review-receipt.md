# 简短审阅回执

适用：方法选择、结果解释或重要修改需要留下可接续判断时。普通实现细节不强制逐项生成回执；以链接复用已有证据，避免复制整份报告。

## 最少内容

```json
{
  "subject": {"artifact_id": "example-report", "version": "example-revision"},
  "reviewer": {"kind": "agent", "id": "example-reviewer"},
  "date": "2026-10-12",
  "scope": "本次检查了什么",
  "evidence_refs": ["example-evidence-id"],
  "judgment": "supported_with_limits",
  "reason": "依据与判断之间的关系",
  "uncovered": ["本次未检查什么"],
  "human_acceptance": "not_recorded"
}
```

示例不是真实审阅。具体判断可为supported、supported_with_limits、unsupported、insufficient_evidence或not_applicable；研究者可以不同意程序检查结论，须保留双方理由。版本变更后原回执留在原对象上，是否适用于新版需重新判断。

## 来源与材料状态

材料应分别登记，避免“全文已读”混用多个含义：

- 完整作者稿：版本、范围、是否读完；没有则unknown。
- 最终发表正文：版本、范围；未取得不能用作者稿代填。
- 补充：哪些已读、哪些未取得、哪些只读文字而未核图。
- 官方实现：实际固定版本、检查入口、后续代码与原实验的差别。
- 实际执行：运行ID、执行记录；静态代码存在不能填成已执行。

## 子项缺口记录

整项partial下面的每条候选缺口记录：适用条件 → 预期义务 → 观察行为 → 证据与限定范围 → 判断 → 影响。判断区分实现缺项、正确性缺陷、资料不足、合理替代或待决选择。没有读到上游实现不能推断整个研究未做。

该回执细化已有Review，不是新的顶层对象；其字段尚未接入工作台或自动校验器。[设计依据D20](../design/11-case-driven-engineering-guidance.md)；[公开研究设计与方法索引](../../../docs/REFERENCES-AND-DESIGN.md)。人的接受只记录真实反馈，不由Agent或检查器自动产生。

## 自动检查范围的附加记录

承接[20例迭代D29](../design/14-twenty-case-relations-and-evidence.md)，审阅能力或设置验收时分别列：

1. **记录一致性**：对象/版本/引用、声明分母、阶段用途等机械约束。
2. **内容与执行**：实际文件、数值、载入/算子回执及其捕获上下文。
3. **科学判断**：独立性假设是否充分、主张能否外推、替代解释是否排除及人的接受。

每项写明触发条件、最少证据、输入与判定、实现入口与状态、实际回执、合法替代和仍需研究者作出的判断。未实现的检查应说明状态。输入为空时，先说明该空集合是否适用于当前问题；区分数学性质和研究实际提供的证据。

比较新旧框架时须固定共同义务范围，保留原判及新版本理由。本文是新的指导补充，不回写历史rubric、冻结计划或旧案例分数。

## 收到请求后的具体验收

按[回应路线与验收](guides/revision-responses-and-acceptance.md)记录选中的处理路线、该路线支持的主张及剩余问题。验收至少说清具体输入、判别操作、产物位置、什么证据会改变决定，以及材料缺失时的结果。补解释／限域与补数据／新验证使用各自材料，不能套用同一全量输入清单。

若使用[research-revision-v1](revision-contract.md)，每个自动实现绑定record_contract或artifact_or_runtime作用范围；manual的审阅者、报告结果和回执另列。旧版简短Review回执继续可用；新检查器不会自动理解本页自由文本或把记录通过转为科学接受。
