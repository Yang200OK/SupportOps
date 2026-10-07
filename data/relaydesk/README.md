# RelayDesk 自建演示资料

这 16 份资料由本项目编写，按 CC0-1.0 提供。它们是后续本地演示软件的设计契约，不是外部产品文档或已验证的运行观测。清单中的 runtime_verified=false 必须保留，实际故障实验安排在第 2 阶段第 3 轮。

三个版本各包含产品说明、配置说明、升级差异、排查手册和一份只有症状的构造案例 JSON。2.0 另有一页文本 PDF 速查。所有原始字节 SHA-256、版本、来源、日期与分发许可都在 manifest.json。

主要区别：1.0 的 delivery_timeout_ms 默认 2000、连接池 5；1.1 同名超时默认 3000、连接池 10；2.0 将下游超时键改为 downstream_timeout_ms。文档默认值不能证明运行实例的实际配置。

网页导入时 source_key、title、product_version、source_type、license、filename、format 按清单填写。相同来源需保持文件名、格式、来源类型与许可；新字节新增修订，同字节复用原修订，标题也沿用原记录。需要独立来源身份时使用不同 source_key。

JSON 案例字段固定为 schema_version、product、product_version、title、description、source_type；不含答案、根因或故障注入标签。未来评测标注存放在独立评测侧。
