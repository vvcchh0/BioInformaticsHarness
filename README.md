# BioInformaticsHarness

BioInformaticsHarness 帮助研究者和 agent 开展生物模型研究：从问题、数据和方法选择开始，完成计算、评价、解释与交接。

项目把模型论文、开源实现、同行评审和小型研究中的经验整理为方法指南，并提供处理研究工件的 Python 工具。当前公开原型包含两个可在 CPU 上运行的保存模型应用。

## 开始使用

先取得项目，再按本轮研究或工具使用需要选择入口：

```sh
git clone https://github.com/vvcchh0/BioInformaticsHarness.git
cd BioInformaticsHarness
```

文献调研与理论比较，从本轮问题、已有课题记录和预期交付开始，在[方法指南](core/docs/methods/README.md)中按当前决定选择入口。问题尚待成形时读[前期准备](core/docs/methods/guides/research-preparation.md)；需要先看清领域时读[综述优先](core/docs/methods/guides/review-first-preparation.md)。问题已明确时，沿相关指南回到原文与实际研究。

受托重新开发一种方法时，从[尚待检验的设计选择](core/docs/methods/guides/research-guidance-cycle.md#从已有问题重新开发方法)组织第一次计算。

需要资料获取、交接、诊断或证据整理工具时，按[工具快速入门](core/QUICKSTART.md)准备 Python 3.11 或更新版本并选择对应命令。运行真实模型应用时，按案例说明安装科学计算依赖。

## 示例

- [Splice](examples/splice/README.md)：用 DNA 序列识别剪接边界，读取保存的模型并处理一组留出样本。
- [Yeast](examples/yeast/README.md)：用蛋白质特征预测酵母蛋白的细胞定位，查看类别预测和决策分数。
- [首次审计](core/examples/research-support/first-audit/README.md)：盘点一个公开数据归档，并保存执行时使用的程序和配置。

## 文档

| 想了解什么 | 从这里读起 |
| --- | --- |
| 怎样把综述推进到研究与后续决定 | [研究推进主线](core/docs/methods/guides/research-guidance-cycle.md) |
| 怎样准备一个新课题 | [前期准备与模板](core/docs/methods/guides/research-preparation.md) |
| 怎样先完成一份详实综述 | [综述优先路线](core/docs/methods/guides/review-first-preparation.md) |
| 怎样取得已选文献与原件 | [资料获取入口](core/docs/methods/source-acquisition-routing.md) |
| 怎样选择研究方法 | [方法指南](core/docs/methods/README.md) |
| 怎样比较方法并安排原文重读 | [方法取舍与视角切换](core/docs/methods/guides/review-choice-synthesis.md) |
| 最新指南在哪些案例中得到检查 | [重读与生物论证链研究总结](docs/research/rereading-validation-2026-10-27.zh.md) |
| 想按具体情境回看研究经验 | [研究心得（可选）](docs/research-insights.md) |
| 怎样向人解释结果 | [生物学解释与交接](core/docs/methods/guides/biological-explanation-and-handoff.md) |
| 参考了哪些工作，怎样用于设计 | [参考来源与设计索引](docs/REFERENCES-AND-DESIGN.md) |
| 项目怎样发展至今 | [项目短史](docs/history/EVOLUTION.zh.md)与[版本记录](CHANGELOG.md) |
| 已完成哪些实际应用 | [研究与应用](core/EVIDENCE.zh.md) |
| 怎样更新进行中课题所用的 Harness | [版本接收、重读与适配](docs/harness-updates.md) |
| 怎样继续开发 | [下一阶段](ROADMAP.zh.md)与[文档约定](docs/DOCUMENTATION.md) |

## 参与

欢迎提出研究问题、改进方法说明或提交小型应用。描述改动时，请说明要解决的问题、选择方法的理由，以及观察到的结果。

## 许可与致谢

代码采用 Apache-2.0，原创说明采用 CC BY 4.0。案例数据和模型文件的具体归属见[许可说明](LICENSING.md)；研究来源见[参考索引](docs/REFERENCES-AND-DESIGN.md)。

已发布版本可从 [Releases](https://github.com/vvcchh0/BioInformaticsHarness/releases) 取得。面向开发工具和 agent 的结构化资料集中在 [machine](machine/) 目录。
