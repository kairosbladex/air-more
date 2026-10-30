# 区域日报生成器验证记录

验证日期：2026-09-09。实现版本：`4ba7e3a`，数据包版本 `1.0`，模板版本 `regional-1.0`。

结论：生成器、输入模板、合成样例、缺失样例和参考件回放已完成本机验证。真实业务数据尚未验收；所有合成和转录输出均显式标明用途，不能作为东昌府本期核定结果。

## 自动验证

| 验证对象 | 结果 | 范围 |
|---|---|---|
| 当前工作区完整测试 | PASS，109 项，17.28 秒 | 56 项既有测试与 53 项区域日报测试 |
| 独立提交快照完整测试 | PASS，107 项，17.27 秒 | `git archive 4ba7e3a`；比工作区少的 2 项属于用户原有未提交测试 |
| 独立快照 CLI 实际导出 | PASS | 合成包产生 DOCX、PDF、JSON，状态 complete |
| 最终六组输出与输入包对照 | PASS | 输入记录、哈希、时段、展示对象与重新构建的结果一致 |
| 最终 DOCX/PDF 文件哈希 | PASS | 逐份匹配各证据 JSON 中 artifact_sha256 |
| 最终 PDF 文本与字体 | PASS | 四个正文章节与附录齐全；嵌入 STSongti-SC-Regular/Bold |
| 原始参考文件只读校验 | PASS | 原件 SHA-256 未改变 |

测试覆盖：跨月跨年、输入混入其他日期、无月累计、无预报、全缺测、贡献率缺项和全零、两种贡献率口径、单位错误、非有限数、重复记录、排名范围冲突、过期图片、未知对象、路径越界、结构类型错误、建议中的裸数字和跨对象引用、DOCX 数值及图片篡改、并发锁、同名拒绝覆盖和转换失败不发布。

DOCX 回读逐项验证正文、表头、单元格及媒体顺序和内容；报告展示对象由已校验事实构建并记录引用。生成器不以数值正则匹配代替对象、日期、指标和来源绑定。

## 版式与真实转换

最终交付样例位于 `out-regional/accepted/`。此前其他输出目录是调试迭代，不作为交付依据。

| 子目录 | 表内对象数 | 实际 PDF 页数 | DOCX 渲染页数 | 生成状态 |
|---|---:|---:|---:|---|
| synthetic | 4 | 4 | 4 | complete |
| missing | 1 | 3 | 3 | complete_with_gaps |
| reference | 28 | 6 | 6 | complete_with_gaps |
| layout-1 | 1 | 3 | 3 | complete |
| layout-28 | 28 | 6 | 6 | complete |
| layout-50 | 50 | 7 | 7 | complete |

六份实际 PDF 的 29 页经 pdftoppm 栅格化逐页检查；同六份 DOCX 经文档工具 render_docx.py 渲染的 29 页也逐页检查。中文无缺字，长站名正常换行，跨页表头重复、单行未拆分，重点底色和已知参评总数下的末位颜色正确，图与图注相邻，无截断、重叠或孤立标题。参考件回放末页仅有三条缺失说明，保留自然分页。

本机应用使用 Python 3.13、LibreOffice 26.2.2.2；独立文档渲染工具使用 LibreOfficeDev 26.8.0.0.alpha0。字体文件为 `/System/Library/Fonts/Supplemental/Songti.ttc`，Word 字体家族为 `宋体-简`，fontconfig 配置为 `/opt/homebrew/etc/fonts/fonts.conf`。已修复英文家族名被原生转换器静默替换，以及 Heading 样式主题字体覆盖的问题。更换运行环境后需要重新进行字体与分页验收；未验证 Microsoft Word/WPS 的实际显示。

## 参考件回放

只读原件：`东昌府区日报（第244期）.docx`。

SHA-256：`9cc35f63a9d38b8871a99d9bb8f9da8469a3430f21abd827a346d493dfc94843`。

回放保留 28 个表内对象、昨日及月累计的原值和顺序、两个重点园区、月贡献率标签及三天文字预报。区政府昨日综合指数按原件导入为 2.16；两园区全市名次 125、85，合并综合指数 2.53、全市名次 112 均按输入展示。合并结果独立绑定对象，不参与区内站点表排序。

原件昨日多项统计为空、园区单项名次为空、昨日饼图无数据；回放保留缺失。参评总数未经明确核实，回放不以 28 行推算总数或末位颜色。原件不具备完整、可核实的五天预报及气象图片发布时间元信息，回放只转录三天文字预报，其他内容说明缺失；不使用日期不一致的旧截图补全。回放使用的发布截止时刻是测试约定，并在来源中说明。

## 使用与交付

```bash
.venv/bin/python -m airmore.cli generate-regional \
  --bundle data/regional/synthetic/manifest.json \
  --out out-regional/reports
```

填写入口：`data/regional/template/manifest.json`。字段说明：`docs/regional-report.md`。完整、缺失与转录数据包分别位于 `data/regional/synthetic/`、`data/regional/missing/`、`data/regional/reference/`。变化对象数量的样例由 `scripts/regional_examples.py` 生成。

所有产物在独立临时目录完成后发布，同名默认拒绝覆盖。complete 代表输入满足生成器校验要求，不代表业务数据已独立核定。真实业务验收还需提供本期区域固定站核定统计、排名范围与参评总数、预报及图片元信息、业务建议，并逐项与核定来源复核。

现有 A/B/G 入口保持兼容；用户原有未提交修改保留。本轮仅本地提交，不推送、不部署，不引入正式小时统计算法、自研预测、在线抓取或 LLM。
