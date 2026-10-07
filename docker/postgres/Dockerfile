# 只复制同一 PostgreSQL 主版本的扩展，保留已经验证的服务版本。
FROM pgvector/pgvector:0.8.7-pg17-bookworm@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d AS extension
FROM postgres:17.11-bookworm
COPY --from=extension /usr/lib/postgresql/17/lib/vector.so /usr/lib/postgresql/17/lib/vector.so
COPY --from=extension /usr/share/postgresql/17/extension/vector* /usr/share/postgresql/17/extension/
