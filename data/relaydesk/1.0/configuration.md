# RelayDesk 1.0 配置说明

> 来源：自建演示产品设计资料。适用 RelayDesk 1.0。许可 CC0-1.0。日期 2026-10-04。
> 本轮尚未实现或验证 RelayDesk 的实际运行行为，以下是后续实验必须验证的设计契约。

## 配置表

| 参数 | 默认值 | 单位 | 用途 |
| --- | --- | --- | --- |
| delivery_timeout_ms | 2000 | ms | 下游请求超时 |
| db_pool_size | 5 | 连接 | 数据库最大连接数 |
| db_pool_wait_ms | 1000 | ms | 获取连接的等待上限 |
| cache_ttl_ms | 60000 | ms | 目标配置缓存有效期 |

## 配置样例

```ini
product_version=1.0
delivery_timeout_ms=2000
db_pool_size=5
db_pool_wait_ms=1000
cache_ttl_ms=60000
```

## 单位与校验

以上时长均为毫秒，不能把秒的数值直接写入。连接池大小为正整数。无效键、非正超时或错误版本按设计应拒绝启动并产生 RD_CONFIG_INVALID。

## 取证边界

文档默认值不能证明当前实例使用相同值。调查需读取实际配置与版本信息。
