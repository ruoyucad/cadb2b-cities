# cadb2b-cities（市级采购管线）

安省市级公共采购（实物商品 GOODS）线索管线 —— cadb2b 高阶版（顾问/佣金模式）的买家层，
补在联邦 CanadaBuys 数据之下。**本目录即未来的独立仓库 `cadb2b-cities` 的根内容**：
代码自包含，唯一的外部耦合是与父项目共享的 DuckDB 文件（见下）。

设计依据：`municipal-procurement-map.md`（门户地图与过滤规则，父仓库根目录；
独立成仓时请一并带走该文件）。

## 数据源

| 源 | 内容 | 机制 |
|---|---|---|
| bids&tenders 集群（Peel / Mississauga / Brampton / York / Durham / Halton） | 在招 + 近期中标/关闭标，详情含分类、品类、中标方与金额（部分机构）、投标人报价（部分机构） | 门户自带 `Tender/Search/{NodeId}` JSON 接口发现 GUID（NodeId/令牌从公开首页 HTML 提取）→ 详情页服务端渲染 HTML 解析 |
| City of Toronto | 中标合同（仅中标，不抓 Ariba 在招） | Toronto Open Data CKAN 数据集 `tobids-awarded-contracts` 的 datastore API |
| bids&tenders 安省外买家（Saskatoon SK / Halifax NS / Moncton NB / St. John's NL / Yellowknife NWT） | 同安省集群 | 同上（同一门户的全国买家，零新增成本） |
| BC 省政府各厅中标（`bc_opendata`） | 仅中标：中标方、金额、中标日期（季度更新） | BC Data Catalogue 数据集 `ministry-contract-awards-...` 季度 CSV。**BC Bid 本站全部页面有 Google reCAPTCHA v3 浏览器校验，不绕过** —— 改走官方开放数据，数据同源 |
| 魁北克 SEAO（`seao_opendata`） | 公告 + 中标：买家、原生分类（goods/services/works）、UNSPSC 品类、中标方与金额（97%+ 有金额） | Données Québec 开放数据 OCDS JSON 周文件（`hebdo_*`）。**SEAO 门户本身对文件/中标详情收费，不抓门户** —— 开放数据同源且免费 |
| Saskatchewan（`sasktenders`） | 在招/已关闭/已中标列表（含机构、标号、起止日期、概要、品类） | 官网服务端渲染列表页（50 条/页）。**中标方与金额在登录后的 Tender Results PDF 内**，公开层拿不到 —— 中标行只入 staging、无金额（同 bids&tenders 薄层） |
| Alberta（`alberta_apc`） | 采购公告 + 中标：原生分类（GD/SRV/CNST）、UNSPSC、中标方、金额、中标日期 | Purchasing Connection 公开 JSON API：`POST /api/opportunity/search`（匿名）发现 → `GET /api/opportunity/public/{年}/{号}` 取中标明细（仅对 AWARD 且 GD 类公告取） |

全部为公开页面/开放数据、无需账号、不绕付费墙与验证码；请求带浏览器 UA 并限速（约普通浏览节奏）。

未接入（已核实，障碍明确）：**Nova Scotia 省门户**（procurement-portal.novascotia.ca 有 F5 TSPD 机器人墙，无公开内容；Halifax 已由 bids&tenders 覆盖）；**Manitoba**（官方 eBidding 为 MERX，文件/中标实质付费）；**BC Bid 本站**（reCAPTCHA，见上，已用开放数据替代）；安大略以外的多数中小城市自有门户多为 MERX/Bonfire，暂不逐个接入。

## 运行

```bash
# 在父仓库 cadb2b-run 内（当前形态）
.venv/bin/python -m muni.scan            # 一轮全量扫描（含全国源，幂等，可重复跑）
.venv/bin/python -m muni.scan --days 21  # 指定中标发现窗口
.venv/bin/python -m muni.scan --sites durham,peelregion --skip-toronto
# 只跑新增省份源（跳过安省与 bids&tenders 全部站点）：
.venv/bin/python -m muni.scan --days 120 --skip-toronto --sites __none__
# 单源开关：--skip-national / --skip-bc / --skip-seao / --skip-sask / --skip-apc

# 独立成仓后：把本目录内容置于仓库根，依赖只有 duckdb + requests
pip install duckdb requests
python -m muni.scan        # 或 python muni/scan.py
```

定时任务（每周两次，建议周一/周四，与内容周更错开）：

```cron
23 7 * * 1,4  cd <repo> && .venv/bin/python -m muni.scan >> muni/scan.log 2>&1
```

## 配置

| 环境变量 | 含义 | 默认 |
|---|---|---|
| `MUNI_DB_PATH` | 共享 DuckDB 路径 | `<仓库根>/data/cbintel.duckdb` |
| `MUNI_REPO_ROOT` | 仓库根（推导 DB 默认路径用） | 本包的父目录 |

独立成仓后，把父项目的 `cbintel.duckdb` 放到 `./data/`，或用 `MUNI_DB_PATH`
指向共享的那一份 —— 供应商关联（`supplier_key()` 与联邦 `dim_supplier` 对齐）
依赖同一份库。

## 数据模型（只增不改，不动联邦表）

- `stg_muni_tenders`：一标一行快照追加（门户、机构、标号、GUID、标题、原生分类、
  品类原文、状态、发布/截止/中标日期、中标供应商与金额、全部投标人 JSON、计划参与人数）。
- `ref_muni_category_map`：门户品类/标题关键词 → UNSPSC 大类（segment）的命中记录。
- `mart_muni_goods_leads`：经纪线索 —— 最新快照中满足「分类 = Goods（多伦多为
  High Level Category 含 Goods）+ 白名单品类 + 中标额 ≥ CAD 25,000 + 硬排除未命中」
  的一标一行，字段对齐 `fct_contract` 口径（buyer / title / value_cad / award_date /
  supplier_key），并标注与联邦库的疑似同单 `possible_dup`（只标记不合并）。

## 已知限制

- 部分机构（如 Durham）把中标结果放在详情页的 Advertisement 页签动态加载，
  主详情页只写 "No Awarded Vendors or refer to the Awarded Results…" —— 这类标的
  中标方/金额抓不到（Peel 等机构是内嵌 Awarded Summary，可正常解析）。详见 BUILD-REPORT.md。
- Mississauga 计划 2026 年 Q3 迁往 Bonfire，新门户解析器待迁移完成后另做。
- MERX（渥太华）、Ontario Tenders Portal / OECM 未接入（二期）。
- BC 开放数据无 Goods/Services 原生字段，中标行按标题关键词分类（services 占比高，
  Goods 命中率低属正常）；且为季度发布，时效性弱于其他源。
- SEAO 周文件为增量发布：`--days` 窗口之外公告的后续中标更新若落在窗口内会被收录，
  反之窗口外的公告即使窗口内中标、若周文件未包含该 release 也会漏检 —— 稳态 21 天
  扫描靠周文件滚动覆盖，影响有限。
- Alberta APC 以 post/close 日期范围检索：发布早于窗口、且关闭也早于窗口的历史公告
  若在窗口内才补登中标，可能漏检；AWARD 专项检索（post 放宽 400 天 + close 落窗口内）
  已覆盖绝大多数情形。
