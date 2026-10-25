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
