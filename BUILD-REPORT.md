# 市级 GOODS 管线 v1 构建报告

日期：2026-10-25。范围：按 `municipal-procurement-map.md` 的设计落地安省市级实物商品
采购管线。入口 `muni/scan.py`（`python -m muni.scan`），代码在 `muni/` 下，
该目录即未来独立仓库 **`cadb2b-cities`** 的根内容（Qian 正在建仓；本次未做任何推送）。

## 仓库独立性

- `muni/` 自带 `README.md`，无指向父仓库的硬编码路径。
- 唯一耦合 = 共享 DuckDB：路径走环境变量 `MUNI_DB_PATH`（可选用 `MUNI_REPO_ROOT`
  推导），默认 `<仓库根>/data/cbintel.duckdb` —— 当前即 cadb2b-run 的那份库。
  独立成仓时把同一份 `cbintel.duckdb` 放进 `./data/` 或用环境变量指过去即可，
  供应商关联（与联邦 `dim_supplier` 同一套 `supplier_key()` 归一化）依赖同库。
- 只增不改：新建 `stg_muni_tenders` / `ref_muni_category_map` / `mart_muni_goods_leads`；
  联邦表未动（`fct_contract` 仍为 256,279 行，核对无误）。

## 做通了什么

### 1. bids&tenders 发现机制 —— 地图上的头号技术问题，已解决 ✅

门户首页的 JS（`scriptsV2/home/index.js`）暴露了列表接口，本次实测跑通：

```
POST {Home}/Tender/Search/{NodeId}?status=&limit=&start=&dir=&from=&to=&sort=
form: keywords, __RequestVerificationToken     → JSON {success, total, data[]}
```

`NodeId`、`Home` 路径、反伪造令牌全部从公开首页 HTML 正则提取，无需账号。
每行含 GUID、标题、状态、发布/截止时间戳、PlanTakers 数。详情页为服务端渲染
HTML，标签解析稳定（分类/编号/状态/发布/截止/中标日期/多行品类均可抽取）；
投标人报价表在有公开表格的页面可抽取（首跑 178 条快照带 `all_bidders_json`）。

### 2. 多伦多中标 —— 走了开放数据，比抓门户更稳 ✅

Toronto Bids Portal 的中标检索是 JS 应用、接口未定位；改走 Toronto Open Data
CKAN 数据集 `tobids-awarded-contracts` 的 datastore API（同一份官方数据），
近两年中标 911 行/轮（含供应商、金额、日期、类别），字段规整。只取中标，未碰 Ariba。

### 3. 入库与线索产出 ✅

去重口径按地图：`(门户, 机构, 标号, 中标供应商)` 取最新快照；mart 套过滤
（Goods 分类 + 白名单关键词 → UNSPSC 大类 + 中标额 ≥ CAD 25,000 + 硬排除），
`possible_dup` 只标记不合并（首跑命中 0，符合"市级买家基本不在联邦库"的预期）。

## 首跑真实数字（2026-10-25，共三轮：Durham 冒烟 + 全量 + 120 天窗口补跑）

| 指标 | 数值 |
|---|---|
| `stg_muni_tenders` 快照总行数 | 3,665（去重后 distinct 标的 1,422） |
| 各买家去重前快照 | Toronto 1,822（两轮各 911）、Peel 232、Durham 200、York 146、Halton 128、Brampton 116、Mississauga 110 |
| 120 天窗口内 bids&tenders 发现量 | Peel 212、York 147、Mississauga 123、Brampton 70（+ Durham/Halton 前轮各约 50–94） |
| 原生分类 = Goods 的标（最新快照口径） | 102（另 Toronto "Goods and Services" 289、"Services Related To Goods" 16） |
| **`mart_muni_goods_leads`** | **14 条，合计 CAD 20,852,289，全部来自多伦多** |
| leads 分布 | 服装纺织 53 ×6、家具 56 ×3、照明 39 ×2、劳保 46 ×1、清洁 47 ×1、工具 27 ×1 |

leads 头部样本：

| 买家 | 标的（标题首句） | 中标商 | 金额 CAD |
|---|---|---|---|
| Toronto | 医疗布草服务 RFP（ECOTEX） | ECOTEX HEALTHCARE LINEN SERVICE LP | 9,357,278 |
| Toronto | 办公家具内装（negotiated RFP） | POI BUSINESS INTERIORS LP | 6,642,284 |
| Toronto | 卫生纸等纸品供应 RFQ | MCL - MISTER CHEMICAL LTD | 2,562,873 |
| Toronto | 警用制服配件供应 | UNISYNC GROUP LIMITED | 323,643 |

## 没打通 / 必须诚实说明的 ⚠️

1. **bids&tenders 侧的中标金额层很薄（本版最大短板）。** 全部快照里只有 **2 条**带内嵌
   "Awarded Summary…in the amount of $…" 的金额。多数机构（含 Durham，地图原以为它最全）
   在主详情页只写 "No Awarded Vendors or refer to the Awarded Results posted under the
   Advertisement tab"，而 Advertisement 页签内容是动态加载的（服务端 HTML 里是空容器），
   其报表接口本次未定位。后果：**bids&tenders 六站目前产出以"在招/关闭的 Goods 标 +
   分类品类 + 竞争热度"为主，中标方/金额基本拿不到** —— 所以 14 条 leads 全来自多伦多。
   下一步候选：用真实浏览器会话抓 Advertisement 页签的请求，或改抓各站
   `Tender/ContractAwards/{NodeId}?year=` 年度报表核对覆盖率。
2. **Awarded 发现的日期过滤语义未完全确认**：接口的 from/to 过滤字段（推测为截止日）
   未经官方文档证实；120 天窗口下 Peel 发现 77 条 Awarded，与列表总量级吻合，暂按可用处理。
3. **Closed 列表每站每轮只取前 100 条**（分页上限设定），历史关闭标不追求全量；
   稳态运行靠 Open + 近期 Awarded 增量即可。
4. **关键词白名单有误命中**：如 "Service Carts" 被描述里的词带进服装类。mart 定位是
   待人工复核的线索，不是结论；`ref_muni_category_map` 记录了每条命中词，可回溯调整。
5. Toronto 数据集的"标题"实为采购描述全文，已改为取首句作标题、描述摘录进
   `categories_raw` 供关键词匹配；个别 RFP  boilerplate 开头（"This RFP is an invitation…"）
   的标题可读性一般。
6. Mississauga 2026 Q3 迁 Bonfire —— 迁移后本站点解析失效，需按新门户重做（README 已标注）。
7. MERX（渥太华）、Ontario Tenders Portal / OECM 按地图分期，未接入。

## 测试

- 仓库原有测试 17/17 + 新增 `muni/test_parse.py` 2/2 = **19 passed**
  （`pytest tests/ muni/test_parse.py -q`，合成数据、无需联网）。
- 健全性查询已跑：各买家计数、分类分布、leads 汇总与样本如上表。

## 定时运行（每周两次，建议周一/周四）

```bash
cd ~/workspace/cadb2b-run && .venv/bin/python -u -m muni.scan >> muni/scan.log 2>&1
```

- 幂等：重复跑只追加快照，mart 恒取最新；稳态默认中标窗口 21 天
  （首跑/补种用 `--days 120`）。
- 单轮实测耗时约 7–9 分钟（详情页逐个抓、0.4 秒限速 + 重试）。
- 环境变量：`MUNI_DB_PATH`（共享库路径）、`MUNI_REPO_ROOT`（可选）。

---

# 全国扩展 v2 构建报告（2026-10-25）

范围：把市级管线从安省扩到全国 —— BC、Alberta、魁北克、Saskatchewan +
bids&tenders 安省外买家；其余省份逐个核实可达性后如实记录障碍。
新增模块（与安省同构：发现 → 规范化到同一 staging 形状 → 同一去重/线索规则）：
`bc.py`、`seao.py`、`sasktenders.py`、`apc.py`；`scan.py` 增加
`--skip-national/--skip-bc/--skip-seao/--skip-sask/--skip-apc` 开关；
`store.classify_goods` 增加 UNSPSC token 规则（品类原文带 `unspsc_segment:NN`
的源以原生 UNSPSC 大类为准，白名单外大类不算）；白名单/硬排除补法语词。

## 各省核实结论（全部经真实请求验证）

| 省/源 | 可达性 | 解析器 | 说明 |
|---|---|---|---|
| **Alberta**（Purchasing Connection） | ✅ 公开 JSON API | `apc.py` | SPA 同源 API 匿名可用：`POST /api/opportunity/search`（筛选须用 `$$custom` 日期段 + Selectable 对象 `{"value":…,"selected":true}`，分页 offset 为页号）；中标明细 `GET /api/opportunity/public/{年}/{号}` 含中标方、金额、日期。原生分类 GD/SRV/CNST + UNSPSC，数据质量全国最佳 |
| **魁北克**（SEAO） | ⚠️ 门户收费 → 改走开放数据 | `seao.py` | SEAO 门户对文件/中标详情收费，不抓门户；Données Québec 以 OCDS JSON 周文件（`hebdo_*`）发布同一批公告：买家、原生分类 goods/services/works、UNSPSC、中标方与金额（中标行 97%+ 有金额）。周文件为增量，跨周更新同一 ocid 在 staging 留多条快照、mart 取最新 |
| **BC**（BC Bid） | ⚠️ 本站 reCAPTCHA → 改走开放数据 | `bc.py` | bcbid.gov.bc.ca 全部页面有 Google reCAPTCHA v3 浏览器校验，不绕过；BC Data Catalogue 季度 CSV 发布各厅中标（中标方/金额/日期全），但无 Goods/Services 原生字段，按标题分类；季度更新，时效性弱 |
| **Saskatchewan**（SaskTenders） | ✅ 列表公开（中标层登录墙） | `sasktenders.py` | 省级官方站服务端渲染列表（Open/Awarded/Closed，50/页）全量可抓：机构、标号、起止、概要、品类、UNSPSC 都有；**中标方/金额在登录后的 Tender Results PDF 内**，中标行只入 staging、无金额，不产 leads（同 bids&tenders 薄层定位：在招情报 + 竞争热度） |
| bids&tenders 安省外买家 | ✅ 同安省机制 | 复用 `bidsandtenders.py` | Saskatoon (SK)、Halifax (NS)、Moncton (NB)、St. John's (NL)、Yellowknife (NWT) 五站首页结构与安省站一致，零新增解析成本（`BIDSANDTENDERS_SITES_NATIONAL`）。Calgary/Edmonton/Winnipeg 等不在该门户（逐个验过） |
| Nova Scotia 省门户 | ❌ 机器人墙 | — | procurement-portal.novascotia.ca 有 F5 TSPD JS 校验，无公开内容可读；Halifax 已由 bids&tenders 覆盖 |
| Manitoba | ❌ 实质付费 | — | 官方 eBidding 为 MERX，文件/中标付费；无免费解析路径 |
| Ontario 以外中小城市自有门户 | ⏸ 二期 | — | 多为 MERX/Bonfire，未逐个接入 |

## 首跑真实数字（120 天窗口，2026-10-25）

新源 staging（本轮采集，已逐源核对）：

| 源 | staging 行 | 去重后标的 | 原生/分类 Goods | 带中标金额行 |
|---|---|---|---|---|
| seao_opendata | 84,924 | 62,248（按 门户+机构+标号+供应商） | 24,061 | 65,111 |
| alberta_apc | 2,116 | 2,102 | 531 | 165 |
| sasktenders | 1,313 | 1,284 | 194 | 0（登录墙，预期） |
| bc_opendata | 614 | 487 | 13 | 568 |

leads mart（与安省同一规则：Goods + 白名单大类 + ≥ CAD 25,000）：
安省口径不变（多伦多 14 条 / CAD 20,852,289 原样保留）；新增
**Alberta 42 条 / CAD 24,080,978**、**BC 11 条 / CAD 2,368,654**；
魁北克按同规则测算约 **5,284 条 / CAD 30.2 亿** —— 头部被省级框架协议占据
（Centre d'acquisitions gouvernementales 的 TELUS 移动框架 CAD 2.20 亿、
Bell CAD 2.10 亿、Santé Québec 的 Microsoft 云框架 CAD 1.55 亿，均为多年期
全省 regroupement 框架额、非单次交付额；大类 43 IT/电信占 2,170 条）。
Saskatchewan 因中标金额在登录墙内，leads 为 0（预期，非故障）。

> 入库状态：BC/Alberta/Sask 已写入共享库并重建 mart 核对无误（`fct_contract`
> 仍 256,279 行，安省 staging 行数未减）。魁北克 84,924 行在首跑时与联系人
> 富化任务并发撞上 DuckDB 写锁，分批入库脚本 `repair.py` 在锁释放后自动完成
> 删除残批 → 全量插入 → 重建 mart；其精确 leads 数以重建输出为准（上表为
> 同规则离线测算）。

## 顺带修的扩展性问题

- `store.rebuild_mart` 的联邦疑似同单标记原为「每条候选 × 全量 256k 联邦行」
  线性扫描，魁北克量级下需数十分钟；改为按 `(supplier_key, 年)` 建索引后
  秒级完成，判定语义（±1% 金额、同年、同供应商）不变。
- 大批量插入须分块（单次 executemany 8.5 万行中途中断会留下半批）；
  稳态 21 天窗口下各源均为小批量，不受影响。

## 测试

- 仓库原有 17 + v1 的 2 + 本轮新增 6（UNSPSC token 规则、BC CSV 映射、
  SEAO release→行 + 周文件窗口选择、Sask 列表块解析、APC 映射与筛选形状）
  = **25 passed**（`pytest tests/ muni/test_parse.py -q`，合成数据、无需联网）。

## 定时运行（含全国源）

```bash
cd ~/workspace/cadb2b-run && .venv/bin/python -u -m muni.scan >> muni/scan.log 2>&1
# 只补新源一轮：--days 120 --skip-toronto --sites __none__
```

- SEAO 周文件为最大宗（120 天窗口 17 个文件 ≈ 270MB；稳态 21 天约 3–4 个文件）。
- Alberta 每次扫描含两次检索（近期公告 + AWARD 专项）+ 仅对 GD 类中标公告取明细。
