# 按时间生成机库空气质量报告

## 启动与分工

这是新增的 air-more 服务入口。原 `airmore` CLI 和区域报告保持兼容。
业务后端负责前端登录、项目/机库访问权限和授权的 requester_id；本服务只接受受信业务后端，
不提供浏览器可用的服务密钥。服务校验机库确属请求项目，下载同时绑定调用方与请求人。

安装及启动（Python 3.11+，Linux/macOS；单worker采用POSIX文件锁）：

```bash
python -m pip install '.[service,service-test]'
# 复制 config/air-report.env.example 到不受版本控制的 .env，填入本机环境配置。
# 只执行自己维护的环境文件；密码含特殊字符时按 shell 字符串规则加引号。
set -a
. ./.env
set +a
uvicorn airmore.service.api:create_app --factory --host 127.0.0.1 --port 8056
# 另一个终端载入相同环境文件，使用相同 AIRMORE_STATE_DIR：
airmore-worker
# 或 python -m airmore.service.worker --once
```

API 与 worker 是独立进程，API 启动不会访问业务库、调用模型或自动执行任务。
默认监听本机地址；发布到内网时由受控网关提供 TLS 和访问控制，不公开暴露此接口。
API 文档在 `/docs`，OpenAPI JSON 在 `/openapi.json`。默认文档只含接口定义，不含配置值。
第一版只支持本机文件系统 SQLite，不将状态目录置于 NFS，不横向启动多个worker。

业务 MySQL 使用专用只读账号更便于运维。实现本身只发起只读事务、SELECT 和连接级 SET，
不会创建账号、索引、任务或其他业务记录。实际账号的权限管理属于部署配置，本项目不自动修改。
当前只支持密码连接与内网 MySQL；如环境要求数据库 TLS，应在连接适配层配置证书后部署。

## 创建任务

```http
POST /api/v1/air-reports
Authorization: Bearer <服务密钥>
Idempotency-Key: 业务后端生成的唯一请求键
X-Requester-Id: trusted-user-id
Content-Type: application/json
```

```json
{
  "siteIds": ["site-1"],
  "reportType": "DAY",
  "startDate": "2026-08-31",
  "endDate": "2026-09-01"
}
```

- 日期必须为 `YYYY-MM-DD`，北京时间，包含起止两天；时间戳与额外字段被拒绝。
- `siteIds` 必须非空且不能重复；同一集合会排序后参与幂等校验。
- 默认最多366天、50个机库；查询还会覆盖比较期，总采样超过50万条时明确失败，不截断后出报告。
- `X-Requester-Id` 请求头必须由业务后端根据已认证用户设置，不能直接信任前端提交的值。
- 服务单位、模板、模型地址、模型名、数据配置均由服务端管理。
- `reportType` 必填，只接受 `DAY`（日报）、`WEEK`（周报）、`MONTH`（月报），写入任务及证据，并决定报告标题。
- 实际统计区间始终以 `startDate/endDate` 为准；不按类型自动扩展、截断或校正日期。上例统计 8 月 31 日和 9 月 1 日两天，主统计 SQL 范围为 `[2026-08-31 00:00:00, 2026-09-02 00:00:00)`。
- 完整自然月与上一自然月比较，其他区间与紧邻之前的等长区间比较；数据查询还覆盖比较期。
- 请求体仅接受上述四个字段；旧 `project_id/hangar_ids/start_date/end_date/requester_id` 请求体不再接受。创建、查询、下载均须可信请求人头。
- 不需传入项目 ID。worker 在查询阶段从当前 `bo_uav_site` 解析项目并复核归属；机库不存在/已删除时任务失败 `INVALID_HANGAR_SCOPE`，跨项目或没有有效项目时失败 `INVALID_PROJECT_SCOPE`。不使用历史备份表替代，业务后端仍负责机库访问权限。
- 机库集合顺序不影响幂等；同键改变报告类型、日期、机库集合或可信请求人返回冲突。旧 SQLite 任务仍可读取（旧任务未记录类型时按 MONTH 处理）。

返回 HTTP 202：

```json
{
  "report_id": "服务生成的标识",
  "status": "queued",
  "stage": "queued",
  "created_at": "带时区的时间",
  "updated_at": "带时区的时间",
  "gaps": [],
  "summary": {},
  "error": null,
  "files": {}
}
```

同一调用方、相同幂等键与相同请求返回同一任务。相同键用于其他请求返回409。
重新生成、重新尝试失败任务或希望使用新数据，需要新幂等键；不会悄悄覆盖旧报告。

## 状态与下载

```http
GET /api/v1/air-reports/{report_id}
Authorization: Bearer <同一调用方服务密钥>
X-Requester-Id: <原请求人>

GET /api/v1/air-reports/{report_id}/files/docx
GET /api/v1/air-reports/{report_id}/files/pdf
```

下载同样带上述两个鉴权头。建议业务后端代理文件流，不把带凭证的下载链接交给浏览器。
状态接口不会提供服务器路径。证据 JSON 保存在私有任务目录，第一版没有对外下载入口。

| 状态 | 含义 |
|---|---|
| queued | 等待worker |
| running | stage为 querying/statistics/writing/rendering |
| succeeded | 所有配置要求齐全的成功结果 |
| succeeded_with_gaps | 文档已完整生成，但有数据或业务配置缺口 |
| failed | 查数、统计、模型、排版或进程中断失败 |

当前版本已提供独立的颗粒物巡测相对指数；其历史覆盖、单位及可比条件见 [计算与建议说明](air-report-relative.md)。正式综合指数、排名及预报仍未启用，正常完成的真实报告通常为 `succeeded_with_gaps`，
这不代表模型失败。模型失败不会降级成模板后标成功。

常见错误：

| code | 处理建议 |
|---|---|
| INVALID_HANGAR_SCOPE | 核对当前项目/机库关系，不能用历史表静默兜底 |
| NO_DATA | 所选范围没有可关联采样，核对日期与采样归属 |
| DATABASE_ERROR | 检查连接、只读权限、表结构和查询超时 |
| TOO_MUCH_DATA | 缩小区间或机库数 |
| MODEL_TIMEOUT / MODEL_UNAVAILABLE | 检查Ollama，重新提交用新幂等键 |
| MODEL_VALIDATION_FAILED | 模型结构/事实校验两次失败，不发布部分报告 |
| MODEL_GPU_UNVERIFIED | 无法确认本次模型在GPU驻留，不冒充GPU分析完成 |
| WORKER_INTERRUPTED | worker重启时发现旧运行任务，将其标为失败 |
| GENERATION_FAILED | 查看服务日志中的任务标识和异常类型，检查字体与转换器 |

状态与文件查询的无权限任务统一返回404；未完成的文件返回409。
服务日志不输出数据库异常原文、模型提示词、密码或原始采样。

## 运行约定与追溯

- 状态目录私有权限，包含 `jobs.sqlite3`、`worker.lock`、`artifacts/<report_id>/`。
- 每份报告先在独立隐藏目录中生成，DOCX回读及PDF转换成功后原子发布。
- worker持有OS锁后才恢复中断状态；不能运行第二个worker来抢占或恢复第一个的任务。
- 优雅退出完成当前任务后退出；强制退出后的运行任务下次启动标为失败，暂存目录被清理。
- 模型超时最多重试一次，内容错误最多修复一次；不无限重试。
- 证据记录统计数据、事实绑定、输入/配置哈希、模型digest及GPU驻留、版本和产物哈希。
- 原始样本、坐标、用户名、备注和密钥不进入模型；模型只得到合成或汇总后的受限事实。
- 没有自动清理历史任务的定时器。维护人员应按组织保留政策清理状态与产物，并保持同步备份。

停止/回滚：停止本服务的API和worker，保留状态目录，将代码恢复到此前版本。
不会涉及业务库回滚，也不需要重启Ollama或宿主机。首次正式部署前替换为组织管理的凭证，
确认字体/LibreOffice与数据库单位配置，并用关联完整的数据重新验收。

## Java后端交接

1. 从登录态得到用户身份，在业务权限系统中校验请求项目和所有机库。
2. 使用稳定业务用户ID设置 `X-Requester-Id` 请求头，过滤客户端额外字段。
3. 用服务端密钥创建报告，将 report_id 与业务用户/项目绑定；创建请求不等待文档完成。
4. 状态轮询建议每2至5秒一次，完成后显示缺失提示或失败原因。
5. 下载时再次检查业务身份，代传服务凭证及原 `X-Requester-Id`，并转发文件流。

配置示例见 `config/air-report.env.example`；统计规则、单位确认文件与标准依据见
`docs/air-report-statistics.md`。代码不包含本次联调的真实账号或密码。

## 本地验收入口

静态OpenAPI快照：`docs/air-report-openapi.json`。分层结果、实际耗时及重现命令：
`docs/air-report-validation.md`。`scripts/verify_air_report_service.py` 的合成模式使用真实GPU，
不使用模型替身；数据库模式仅执行只读汇总。测试输出目录必须单独创建，不覆盖旧产物。

指定单日日报的合成验收（统计当日，与前一日比较；仍调用真实GPU）：

```sh
python scripts/verify_air_report_service.py synthetic \
  --report-type DAY --start-date 2026-09-14 --end-date 2026-09-14 \
  --hangars 2 --output var-airmore/day-preview-new
```

日期与报告类型参数用于合成模式，生成对应区间及比较期的分钟样本。
运行前按环境示例配置模型地址与转换器；文档会明确标记合成数据。
单日图形显示各指标的数据点，不把一个日期画成跨日趋势线。

## 项目指标接口作为单位来源

设置 `AIRMORE_INDICATORS_PROJECTS` 指向私有 JSON 配置文件，按报告解析出的项目 ID
选择接口地址和服务端身份。接口不带查询参数；项目身份必须由现有业务接口认可的
projectId 请求头与登录凭证确定，不能假定一个登录身份适用于所有项目。

配置结构示例（base_url 包含应用前缀 /api，projectId 由 worker 按已解析的项目自动设置）：

```json
{
  "actual-project-id": {
    "base_url": "https://business-api.example/api",
    "headers": {"Authorization": "Bearer REPLACE_WITH_PROJECT_AUTHORIZED_TOKEN"}
  }
}
```

worker 在统计前执行 `GET {base_url}/airmonitoringNew/screen/indicators`，接受
`[{"code":"SO2","name":"SO2","unit":"ppm","sort":1}]` 形式的直接数组，
也接受后端 R 包装：`{"success":true,"code":200,"data":[...]}`；业务失败响应不会当作成功。
仅使用 SO2/NO2/CO/O3/PM10/PM2.5 六类指标，PM2.5 对应采样字段 pm2_5。
接口响应及顺序写入统计证据，模板仍保留原六指标列序；未返回的指标显示缺失。
接口单位取代旧数据库 index_unit 解析；已确认测量配置若与其冲突仍报单位冲突。
接口不自动证明固定点、采样连续性或气体参比状态，不自动开启标准 AQI。

未设置此配置时保持旧数据库单位来源。启用后，项目未配置、接口失败、重复指标、
未知单位和非法响应均明确失败，不回退旧单位。配置应由服务维护者绑定到对应项目；
worker 自动发送解析得到的 projectId；配置若含不同 projectId 会在发送前失败。
响应本身没有项目 ID，登录凭证的访问权限仍由业务后端校验。禁止自动跨地址重定向。
本地契约测试已覆盖字段映射、单位生效、未启用指标、空列表及异常；真实 HTTP 联调
仍需提供实际地址和项目身份配置。

### 聊城分支调用约定核查

2026-09-15 读取远端 `feature/lc-develop-9月`，提交 `b8fa1a23692d9e64433dad75871721dcbfeff827`：

- `sys-core/src/main/resources/config/application-dev.yml`：端口 18081，context-path `/api/`。
- `application.yml`：默认 profile 为 dev，鉴权头 Authorization，前缀 Bearer。
- `AirMonitoringAnalysisController`：类路径 airmonitoringNew，现有查询接收 `@RequestHeader String projectId`。
- `sys-utils/.../common/R.java`：成功响应 success=true、code=200，结果位于 data。
- 因此新指标接口预期路径为 `/api/airmonitoringNew/screen/indicators`。
- 指定远端提交的大气监测模块未找到 screen/indicators 实现；以上是现有调用约定核查，
  不构成新接口存在或已部署的证明。18081 为源码开发配置，不代表任何具体主机的当前监听端口。

未切换或修改后端工作区，未启动后端、执行初始化接口或部署服务。

## 四类来源与本地模板模式（2026-09-16）

`siteIds` 仍是站点主键，不改成机库主键。读取同时核验
`bo_uav_site.hangar_id → bo_uav_hangar.id` 和
`bo_uav_site.uav_id → bo_uav_register_info.id`，三者必须未删除且属于同一项目。
缺失、删除和跨项目绑定返回 `INVALID_DEVICE_LINKAGE`；重复主键导致的 JOIN 放大返回
`RELATION_CARDINALITY_ERROR`。名称仅用于仓库层候选发现，同名必须消歧。

采样入口为 `bo_task_air`，通过已有 `bo_task.id/task_id`、`bo_task.site_id` 关联站点。
`bo_task` 是必要的辅助归属表；`bo_air_quality_index_project` 仍为已有单位配置来源。
不使用日期后缀备份表，不按载荷编号猜无人机 SN，不初始化或写入来源/共享业务表。
当前站点 enable、无人机 data_valid/auth_status 在证据中记录，不将当前审核状态推定为
过去采样发生时的有效性。当前绑定不证明历史换绑关系。

worker 开始取数时冻结带时区的 `as_of`，数据库及统计共用半开区间，上限取报告期结束
与截止时间的较早者。采集字段为 `bo_task_air.time`；沿用现有 Asia/Shanghai DATETIME
存储契约，未据此证明采集端时间同步。证据记录实际截止、最新采集、来源主键 SHA256、
原始条数和关联信息。数据来源时区若另有定义，需先确认并调整适配，不能改用 create_time。

服务端可显式设置 `AIRMORE_WRITING_ENGINE=template`，使用与模型相同的事实绑定校验，
完全本地生成文字。默认仍为 `ollama`；模板模式不调用模型，也不是模型失败自动回退。
报告任务、统计、DOCX/PDF 模板和下载入口复用原服务。
数据库认证/权限拒绝为 `DATABASE_PERMISSION_DENIED`；连接/超时为 `DATABASE_ERROR`；
真实读取成功但无有效采样仍为 `NO_DATA`，三类状态不混为零。

### 产品格式模板统一（2026-09-20）

服务渲染入口 `airmore/service/render.py` 使用
`airmore/service/product_report.py`，区域周期入口使用
`airmore/regional/product_render.py`；两者都从
`airmore/regional/product_style.py` 引用同一份
`airmore/service/assets/product-report-template.docx`。模板版本为
`product-air-quality/2`，SHA-256 为
`79ca5ed271facc4e9d108d191a0cb49c25bec853e0602bd75c4081292af74289`。

站点导出固定使用所选站点的数据库统计、趋势和研判建议；旧环境变量
`AIRMORE_REGIONAL_ENRICHMENT` 不再切换服务报告的数据来源。
城市公开日值不能替代站点采样，独立区域报告入口不受此限制影响。
站点浓度表按站点分别展示实际采样时段统计值和已核验单位；无有效数据
或单位不明确时保留缺口，不套用城市浓度或将巡测均值标为完整日均值。

服务报告版本为 `product-air-quality/2+service/v1`，区域报告版本为
`product-air-quality/2+regional/v1`。测试和生产只允许使用同一代码和模板版本；数据库、状态目录、端口、凭据及字体预检结果属于环境隔离项，不得据此替换版式。缺测指标输出 `/`，不表示零；正式 AQI、排名、污染源贡献率和预报在独立口径或完整证据不足时不生成。

本地统一代码不等于远端已经生效。本轮未重新部署、重启测试服务或修改生产服务；线上生效仍需按既有发布授权和分环境验收流程执行。

### 大气传感器载荷及项目默认单位（2026-09-16 追加）

载荷入口为 `bo_load_register_info`，读取未删除且绑定所选站点无人机的登记记录。
采样按 `(site.uav_id, air.load_sn)` 匹配 `(load.uav_id, load.load_imei)`；只接受
配置的 `AIRMORE_ATMOSPHERIC_LOAD_TYPES`（JSON 数组，默认 `["1003"]`，由本轮真实登记
“大气传感器”验证），并要求采样与登记的 load_type 一致。不同设备的相同序列号不能串用。
一个无人机同一编号对应多个登记时返回 AMBIGUOUS_PAYLOAD，不任选一条，不放大 JOIN。
未匹配/非大气载荷/类型不符的采样按原因排除，写入 provenance.payload_exclusions。
载荷 state 缺少枚举语义，当前只记录，不推测启用/审核意义。历史绑定仍未证明。

`index_unit` 没有 productNum 的条目按项目默认单位处理；明确的产品条目优先，
产品单位为空不得回退默认单位；同一作用域多单位冲突保持 unit_conflict。
不依据编号前缀、品牌相似、数值大小或其他厂家配置推测原始单位。

指标接口可在每个项目配置中设置 `"path":"/screen/indicators"`。
实际请求为 `{base_url}{path}`；不填 path 保留原 `/airmonitoringNew/screen/indicators`。
base_url 应含实际应用/控制器前缀，不得重复拼接；path 只允许上述两个已知路径。
地址和身份需实际配置，源码路径不等于接口已部署或已鉴权。
