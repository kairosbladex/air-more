# 区域空气质量研判日报

区域日报将外部已核定结果转换为可编辑 Word、PDF 和证据 JSON。程序负责日期、单位、来源、排名范围、图文一致性校验，不核定上游统计结果，不计算正式综合指数，不调用 LLM。

## 运行

在项目目录使用现有 Python 环境运行：

```bash
.venv/bin/python -m airmore.cli generate-regional \
  --bundle data/regional/synthetic/manifest.json --out out-regional/reports
```

输出目录下新增一个 `区域_日期_第期号期/` 子目录，里面有同名 `.docx`、`.pdf`、`.json`。三个文件全部完成后才发布目录。同名目录存在时拒绝覆盖；需要重新生成时使用新的输出目录或业务期号。失败返回非零状态，失败暂存文件自动清理。进程被强制终止后可能留下 `.区域_日期_第期号期.lock`，确认对应进程已退出后才能人工移除该锁。

依赖沿用项目的 pandas、matplotlib、python-docx；PDF 需要 LibreOffice 的 `soffice`。中文字体按 Songti SC、SimSun、Noto Serif CJK SC、PingFang SC、Noto Sans CJK SC 顺序预检。macOS 安装了 Homebrew fontconfig 时自动使用其配置；其他环境可以设置 `FONTCONFIG_FILE`。报告记录实际字体和 LibreOffice 版本。

macOS Word 字体使用对应中文家族名（例如 `宋体-简`），图表继续使用字体文件；实测本机 LibreOffice 26.2 使用英文 `Songti SC` 会静默替换字体，影响字形和分页。更换系统、字体或 LibreOffice 版本后，应重新检查实际导出的 PDF，不能用另一转换器的预览页数代替验收。

## 数据包清单

从 `data/regional/template/` 开始填写，完整数据参考 `data/regional/synthetic/`，缺失场景参考 `data/regional/missing/`。这几个包中的日期、站点、来源和期号都是示例，投入使用前必须替换。

| manifest 字段 | 约定 |
|---|---|
| `schema_version` | 固定 `1.0` |
| `data_kind` | `business_import` 业务导入、`synthetic` 合成、`reference_transcription` 转录回放、`grid_model` 模型数据；每份报告只允许一种 |
| `region` | `id`、`name` |
| `report_date` | 发布日期 D，严格 `YYYY-MM-DD` |
| `published_at` | 带时区的发布截止时刻，换算北京时间后必须是 D |
| `issue` | 必填期号，不自动递增；允许字母、数字、下划线和连字符，最长 64 字符 |
| `table_scope_id` | 昨日、月表、重点园区单项排名所使用的比较范围 ID |
| `sources` | 非空数组，每项 `id`、`label`、`kind`、`issued_at`；kind 必须与 data_kind 一致，时刻不得晚于发布截止 |
| `stations` | 非空数组，每项 `id`、`name`、`region_id`、非负整数 `order`、布尔值 `focus`、`kind` |
| 站点 `kind` | `station`、`park`、`combined`；合并对象只出现在工作建议中，不进入站点排名表 |
| `files` | 可选键 `statistics`、`ranks`、`contributions`、`forecasts`、`advice`，值是包内相对路径；不提供的文件按缺失处理 |
| `weather_images` | 可选数组，格式见下文 |

所有 ID 只能使用字母、数字、下划线、连字符，以字母或数字开头，最长 64 字符。名称支持中文。每个记录 ID 在整个包内唯一；同一统计键重复时拒绝导入，包括完全相同的重复记录。所有路径必须指向包目录内的文件，禁止目录穿越和指向包外的符号链接。

## 昨日与月累计

`statistics.csv` 使用长表，每行一个对象的一项统计结果，表头必须完整且不能重复：

```text
id,entity_id,period,start,end,metric,unit,value,quality,reason,source_id,basis
```

- `period=day`：start、end 均为 D−1。`period=month`：start 为 D−1 所在月份月初，end 为 D−1；例如 2026-09-01 发布日报对应 2026-08-31 和 2026-08-01 至 2026-08-31。
- 输入包只接受本期数据；混入其他日或月的记录直接报错，不把整个目录当日统计。
- `metric` 为 `so2/no2/co/o3/pm10/pm2_5/composite`。CO 单位固定 `mg/m³`，其他污染物固定 `μg/m³`，综合指数固定无量纲标记 `1`。不自动换算单位。
- `quality=valid` 时 value 必须为有限非负数；`quality=missing` 时 value 留空，reason 必填。未提供的记录同样显示 `/` 并记录缺失原因。
- `basis` 是必填口径说明，例如 O3 的统计时段或月评价口径。上游需要确保不同记录口径正确；程序不会把演示公式用于正式结果。
- 展示污染物整数、CO 一位小数、综合指数两位小数；JSON 保留输入数值。单位和精度说明在统计表下方展示。

## 排名与贡献率

`ranks.csv` 表头：

```text
id,entity_id,period,start,end,metric,rank,total,scope_id,scope_name,quality,reason,source_id,basis
```

排名是输入结果，不能用行号补齐。rank 为正整数，total 是已知参评总数，可留空；已知 total 时 rank 不得超过 total。同一时段、指标和 scope_id 的范围名称、总数、来源和口径必须一致。并列名次原样保留；同名次以站点 order、ID 稳定排序。缺失名次按配置顺序放在表尾。全市排名使用独立 scope_id，出现在重点对象工作建议中，不参与区内表排序。

重点园区保持黄底；已知 total 且 total≥4 时，后三名黄字、末位红字。为保证黄底可读，黄底上的黄字使用深金黄色；没有总数时不猜测末位，也不根据当前导入行数着色。表中浓度缺失但上游明确提供名次时，保留该导入名次，不声称名次由缺失浓度计算得出。

`contributions.csv` 表头：

```text
id,entity_id,period,start,end,metric,mode,value,quality,reason,source_id,basis
```

每个对象、时段对应六项污染物，不含 composite。同一组必须使用同一种 mode、source_id、basis。`mode=components` 接受六项分指数并除以合计；`mode=ratios` 接受 [0,1] 比例，完整六项合计需为 1（容差 0.000001）。不接受百分数 20 代替 0.2。缺项或合计为零时不画饼图，保留原因。图表 JSON 保存六项比例和各输入记录 ID。

## 预报与气象图

`forecasts.json` 为数组，每天最多一项，日期必须在 D 至 D+4：

```json
{
  "id": "F1", "region_id": "demo", "date": "2026-09-01",
  "source_id": "S1", "issued_at": "2026-09-01T08:00:00+08:00",
  "basis": "业务预报原值导入", "quality": "valid", "reason": "",
  "aqi_min": 46, "aqi_max": 76, "category": "优至良",
  "primary_pollutants": ["o3", "pm10"], "weather": "多云",
  "temp_min": 17, "temp_max": 28,
  "humidity_min": 66, "humidity_max": 77,
  "wind_direction": "东北风", "wind_min": 3, "wind_max": 4
}
```

所有数值区间必须成对提供，最小值不得大于最大值。AQI 是 [0,500] 的整数；温度固定 ℃，范围 [-100,100]；湿度固定百分比 [0,100]；风力固定 [0,17] 的整数等级，不是 m/s。等级、天气和风向字段不能夹带数字，应使用相应数值字段。程序保留上游等级，不重新计算等级或预测数值。

无法提供的范围两个端点均为 null；缺失文字字段为 null；缺失首要污染物为 []；reason 必须说明缺失。quality=missing 时所有结果必须为空。前三天文字与五天表格由相同记录生成；没有某天记录时，保留该日并说明缺失。

每张气象图在清单中填写：`id/path/region_id/source_id/caption/issued_at/valid_start/valid_end`。仅接受本地 PNG/JPEG，必须能解码，时间必须带时区；发布时间不晚于报告截止，有效时段必须与 D 至 D+4 有交集。图内原始文字不会由程序 OCR 核定，需要来源方保证图片与所填元信息一致。报告保留完整有效时段，图片与输入 SHA-256 可追溯。

## 工作建议与证据

`advice.json` 为数组，每个对象最多一项：`id/entity_id/source_id/issued_at/quality/reason/text/evidence_ids`。工作建议只展示 focus 园区和 combined 对象。

业务建议可以是“安排现场核查”。数字必须通过 `{记录ID}` 占位符引用本对象的 valid 统计或排名，并把该 ID 加入 evidence_ids；例如 `参考{P01_month_composite_stat}，安排复核。`。程序替换为该事实的时段、指标、数值和单位，拒绝跨对象引用、未知引用和裸数字。气象预报应进入 forecasts，而不是通过自由建议文字补写。

程序自动总结重点对象昨日和当月的综合指数及各范围名次，正文使用紧凑的 E 编号；证据 JSON 的 `view.citations` 映射回原始记录 ID。缺业务建议时明确显示待补充，不根据排名推导管控动作。

证据 JSON 包含输入快照的 SHA-256、来源、时段、口径、记录、图表比例、展示绑定、缺失项、模板版本、字体/转换器版本及 DOCX/PDF 文件哈希。`complete` 只表示配置要求的数据已提供并通过生成器校验，不表示其业务结论已被独立核定；`complete_with_gaps` 表示成功生成了包含缺失说明的报告。

## 样例与回归

```bash
.venv/bin/python scripts/regional_examples.py --out /tmp/regional-full --count 4
.venv/bin/python scripts/regional_examples.py --out /tmp/regional-missing --count 1 --missing
.venv/bin/python scripts/regional_examples.py --out /tmp/regional-template --template
.venv/bin/python scripts/regional_examples.py --out /tmp/regional-reference \
  --reference '东昌府区日报（第244期）.docx'
.venv/bin/python -m pytest tests/ -q
```

参考件回放保留 28 个表内对象和两园区；合并对象的 2.53/全市112 名仅用于正文。转录饼图标签作为回放数据，不反推统计公式。原参考件没有完整的预报及图片发布时间元信息，因此不复制日期不一致的 AQI 截图，也不把气象图伪装为已核实的本期输入。完整五天和双图布局通过明确标记的合成样例验证。

真实业务验收需要业务方提供本期核定结果和来源说明；当前样例不构成东昌府业务数据已经接通的证据。
