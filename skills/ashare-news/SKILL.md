---
name: ashare-news
description: 列举 akshare 常用接口获取 A股新闻、快讯、公告、研报、情绪与财报日历
---

# A股新闻与事件接口

## 使用场景

用于盘中快讯监控、个股新闻聚合、公告与研报抓取、投资者关系披露跟踪、事件驱动和情绪观察。

**只允许在开盘前、收盘后运行，盘中盯盘禁止使用该技能包**

## 前置提醒

- **东方财富 `eastmoney` / `em` 接口可能失败。** 常见问题是字段漂移、分页返回结构变化、偶发空数据。
- 高返回量接口不要直接全量输出，优先 `head()`、按时间范围过滤、按股票代码过滤。
- 下列“测试条数”来自单次测试，仅用于估计体量，不代表稳定值。

## 依赖

需要安装 `akshare` 和 `pandas`。

## 代码片段

```py
import akshare as ak

df = ak.stock_info_global_em()
print(df.head(20))  # 不要直接 print 全量
```

## 接口清单

### 盘前与盘中快讯

#### `stock_info_cjzc_em()`

- 参数：无
- 返回：`pandas.DataFrame`
- 返回列：`标题(str)`、`摘要(str)`、`发布时间(str)`、`链接(str)`
- 测试条数：`400`
- 备注：返回条目很多，不要全量输出；适合盘前摘要和晨会材料。

#### `stock_info_global_em()`

- 参数：无
- 返回：`pandas.DataFrame`
- 返回列：`标题(str)`、`摘要(str)`、`发布时间(str)`、`链接(str)`
- 测试条数：`200`
- 备注：返回条目很多，不要全量输出；盘中快讯信息密度高，优先级高。

#### `stock_info_global_cls(symbol: str = "全部")`

- 参数：`symbol` 可选 `全部`、`重点`
- 返回：`pandas.DataFrame`
- 返回列：`标题(str)`、`内容(str)`、`发布日期(str)`、`发布时间(str)`
- 测试条数：`3`
- 备注：适合只盯电报流；如果需要更密集的电报，可切回 `symbol="全部"`。

### 个股新闻

#### `stock_news_em(symbol: str = "603777")`

- 参数：`symbol` 为股票代码或关键词
- 返回：`pandas.DataFrame`
- 返回列：`关键词(str)`、`新闻标题(str)`、`新闻内容(str)`、`发布时间(str)`、`文章来源(str)`、`新闻链接(str)`
- 测试条数：`10`
- 备注：适合个股新闻聚合，常能抓到财报、产业链和媒体报道。

### 公告与披露

#### `stock_notice_report(symbol: str = "全部", date: str = "YYYYMMDD")`

- 参数：`symbol` 可选 `全部`、`重大事项`、`财务报告`、`融资公告`、`风险提示`、`资产重组`、`信息变更`、`持股变动`；`date` 为单日日期
- 返回：`pandas.DataFrame`
- 返回列：`代码(str)`、`名称(str)`、`公告标题(str)`、`公告类型(str)`、`公告日期(date)`、`网址(str)`
- 测试结果：失败
- 测试条数：未获取
- 备注：保留。这个接口覆盖全市场单日公告，价值高，但本次测试命中了 `KeyError: '代码'`，疑似上游字段漂移。

#### `stock_individual_notice_report(security: str, symbol: str = "全部", begin_date: str = None, end_date: str = None)`

- 参数：`security` 为股票代码；`symbol` 取值同 `stock_notice_report`；`begin_date`、`end_date` 为日期区间
- 返回：`pandas.DataFrame`
- 返回列：`代码(str)`、`名称(str)`、`公告标题(str)`、`公告类型(str)`、`公告日期(date)`、`网址(str)`
- 测试条数：`18`
- 备注：适合单只股票公告回溯。

#### `stock_zh_a_disclosure_report_cninfo(symbol: str = "000001", market: str = "沪深京", keyword: str = "", category: str = "", start_date: str = "YYYYMMDD", end_date: str = "YYYYMMDD")`

- 参数：`symbol` 为股票代码；`market` 默认 `沪深京`；`keyword` 和 `category` 可留空；`start_date`、`end_date` 为日期区间
- 返回：`pandas.DataFrame`
- 返回列：`代码(str)`、`简称(str)`、`公告标题(str)`、`公告时间(str)`、`公告链接(str)`
- 测试条数：`13`
- 备注：官方披露口径，适合与东方财富公告流交叉验证。

#### `stock_zh_a_disclosure_relation_cninfo(symbol: str = "000001", market: str = "沪深京", start_date: str = "YYYYMMDD", end_date: str = "YYYYMMDD")`

- 参数：`symbol` 为股票代码；`market` 默认 `沪深京`；`start_date`、`end_date` 为日期区间
- 返回：`pandas.DataFrame`
- 返回列：`代码(str)`、`简称(str)`、`公告标题(str)`、`公告时间(str)`、`公告链接(str)`
- 测试条数：`5`
- 备注：适合跟踪投资者关系活动记录、调研纪要披露。

### 研报

#### `stock_research_report_em(symbol: str = "000001")`

- 参数：`symbol` 为股票代码
- 返回：`pandas.DataFrame`
- 返回列：`序号(int)`、`股票代码(str)`、`股票简称(str)`、`报告名称(str)`、`东财评级(str)`、`机构(str)`、`近一月个股研报数(int)`、`2026-盈利预测-收益(float)`、`2026-盈利预测-市盈率(float)`、`2027-盈利预测-收益(float)`、`2027-盈利预测-市盈率(float)`、`2028-盈利预测-收益(float)`、`2028-盈利预测-市盈率(float)`、`行业(str)`、`日期(str)`、`报告PDF链接(str)`
- 测试条数：`527`
- 备注：返回条目很多，不要全量输出；优先按日期或机构过滤。

### 事件驱动

#### `stock_gddh_em()`

- 参数：无
- 返回：`pandas.DataFrame`
- 返回列：`代码(str)`、`简称(str)`、`股东大会名称(str)`、`召开开始日(date)`、`股权登记日(date)`、`现场登记日(date|None)`、`网络投票时间-开始日(date)`、`网络投票时间-结束日(date)`
- 测试条数：`5751`
- 备注：返回条目很多，不要全量输出；适合会前事件扫描和日历驱动。

#### `stock_zdhtmx_em(start_date: str = "YYYYMMDD", end_date: str = "YYYYMMDD")`

- 参数：`start_date`、`end_date` 为日期区间
- 返回：`pandas.DataFrame`
- 返回列：`序号(int)`、`股票代码(str)`、`股票简称(str)`、`签署主体(str)`、`签署主体-与上市公司关系(str|None)`、`其他签署方(str|None)`、`其他签署方-与上市公司关系(str|None)`、`合同类型(str)`
- 测试条数：`57`
- 备注：适合重大合同事件流。

#### `news_report_time_baidu(date: str = "YYYYMMDD", cookie: str = None)`

- 参数：`date` 为单日日期；`cookie` 可选，必要时手动传入
- 返回：`pandas.DataFrame`
- 返回列：`股票代码(str)`、`股票简称(str)`、`交易所(str)`、`财报类型(str)`、`发布时间(str)`、`市值(float)`、`发布日期(date)`
- 测试条数：`200`
- 备注：返回条目很多，不要全量输出；直接对应财报发行日历。

### 情绪

#### `stock_js_weibo_report(time_period: str = "CNHOUR12")`

- 参数：`time_period` 可选 `CNHOUR2`、`CNHOUR6`、`CNHOUR12`、`CNHOUR24`、`CNDAY7`、`CNDAY30`
- 返回：`pandas.DataFrame`
- 返回列：`name(str)`、`rate(float)`
- 测试条数：`50`
- 备注：适合做简化版情绪横截面，不包含原文内容，只给名称和强弱分值。
