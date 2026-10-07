"""独立于旧解析器的确定性切片，所有正文都由原文区间构造。"""

import hashlib
import json
from bisect import bisect_right
from uuid import NAMESPACE_URL, UUID, uuid5

from markdown_it import MarkdownIt

from supportops.chunks.contracts import (
    ChildChunk,
    ChunkConfig,
    ChunkResult,
    ParentChunk,
    SourceSpan,
)
from supportops.documents.contracts import ParsedBlock, ParsedDocument

MAX_ATOMIC = 8000
MAX_CHUNKS = 3000


class ChunkFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def digest(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def snapshot_digest(result: ChunkResult) -> str:
    return digest(result.model_dump(mode="json", exclude={"snapshot_sha256"}))


def locations(parsed: ParsedDocument, format: str):
    lines = parsed.text.splitlines(keepends=True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))
    cursor = 0
    located = []
    for block in parsed.blocks:
        if format == "md":
            if block.line_start is None or block.line_end is None:
                raise ChunkFailure("SOURCE_LOCATION_INVALID", "Markdown 块缺少原文行号。")
            if not 1 <= block.line_start <= block.line_end <= len(lines):
                raise ChunkFailure("SOURCE_LOCATION_INVALID", "Markdown 原文行号不合法。")
            start, end = line_offsets[block.line_start - 1], line_offsets[block.line_end]
        else:
            start, end = cursor, cursor + len(block.text)
            cursor = end + 2
        if not block.text or parsed.text[start:end] != block.text:
            raise ChunkFailure("SOURCE_LOCATION_INVALID", "解析块与固定正文的字面位置不一致。")
        if located and start < located[-1][2]:
            raise ChunkFailure("SOURCE_LOCATION_INVALID", "解析块位置发生重叠或逆序。")
        located.append((block, start, end))
    if format != "md" and cursor - 2 != len(parsed.text):
        raise ChunkFailure("SOURCE_LOCATION_INVALID", "页或字段的解析顺序与正文不一致。")
    if not located:
        raise ChunkFailure("SOURCE_LOCATION_INVALID", "没有可用于切片的解析块。")
    return located


def heading_title(block: ParsedBlock) -> str:
    tokens = MarkdownIt("commonmark").parse(block.text)
    for token in tokens:
        if token.type == "inline":
            return token.content
    raise ChunkFailure("SOURCE_LOCATION_INVALID", "标题内容无法与章节结构对应。")


def section_groups(located, format: str, text_length: int):
    if format != "md":
        return [([item], [], item[1], item[2]) for item in located]
    groups = []
    stack = []
    current = []
    path = []
    start = 0
    for item in located:
        block, block_start, _ = item
        if block.kind == "heading":
            if current:
                groups.append((current, path, start, block_start))
            level = block.heading_level
            if level is None:
                raise ChunkFailure("SOURCE_LOCATION_INVALID", "标题缺少层级。")
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading_title(block)))
            path = [title for _, title in stack]
            start = block_start
            current = []
        current.append(item)
    groups.append((current, path, start, text_length))
    return groups


def windows(start: int, end: int, config: ChunkConfig):
    while start < end:
        stop = min(start + config.max_chars, end)
        yield [(start, stop)]
        if stop == end:
            return
        start = stop - config.overlap_chars


def table_headers(source: str):
    inside = False
    headers = []
    for token in MarkdownIt("commonmark").enable("table").parse(source):
        if token.type == "thead_open":
            inside = True
        elif token.type == "thead_close":
            inside = False
        elif inside and token.type == "inline":
            headers.append(token.content)
    return headers


def structured_parts(source: str, start: int, end: int, kind: str, config: ChunkConfig):
    lines = source[start:end].splitlines(keepends=True)
    ranges = []
    cursor = start
    for line in lines:
        ranges.append((cursor, cursor + len(line)))
        cursor += len(line)
    prefix, suffix = [], []
    body = ranges
    language = None
    headers = []
    if kind == "table":
        prefix, body = ranges[:2], ranges[2:]
        headers = table_headers(source[start:end])
        # 表头与分隔行使用一个连续 span，后面的数据行另有 span。
        prefix = [(prefix[0][0], prefix[-1][1])]
    else:
        token = next(
            t
            for t in MarkdownIt("commonmark").parse(source[start:end])
            if t.type in ("fence", "code_block")
        )
        language = token.info.split()[0] if token.info.strip() else ""
        if token.type == "fence":
            prefix, body = ranges[:1], ranges[1:]
            # 解析器已识别容器中的围栏；正文行数用于判断原文是否有闭合行。
            # 直接复用原始行，因此引用 / 列表前缀与未闭合状态均不被改写。
            if len(lines) == len(token.content.splitlines()) + 2:
                suffix, body = ranges[-1:], ranges[1:-1]
    fixed_size = sum(b - a for a, b in prefix + suffix)
    if fixed_size > MAX_ATOMIC:
        raise ChunkFailure("STRUCTURE_TOO_LARGE", "表头或围栏超过 8000 字符。")
    parts = []
    pending = []
    size = fixed_size
    for row in body:
        width = row[1] - row[0]
        if fixed_size + width > MAX_ATOMIC:
            raise ChunkFailure("STRUCTURE_TOO_LARGE", "不可切断的结构行超过 8000 字符。")
        if pending and size + width > config.max_chars:
            parts.append(prefix + [(pending[0][0], pending[-1][1])] + suffix)
            pending = []
            size = fixed_size
        pending.append(row)
        size += width
    if pending:
        parts.append(prefix + [(pending[0][0], pending[-1][1])] + suffix)
    elif not parts:
        parts.append(prefix + suffix)
    return parts, headers, language


def build_chunks(
    revision_id: UUID, parsed: ParsedDocument, format: str, config: ChunkConfig
) -> ChunkResult:
    config_sha = digest(config.model_dump())
    identity = f"supportops:structure-v1:{revision_id}:{config_sha}"
    set_id = uuid5(NAMESPACE_URL, identity)
    result = ChunkResult(
        chunk_set_id=set_id,
        revision_id=revision_id,
        config=config,
        config_sha256=config_sha,
        parsed_sha256=hashlib.sha256(parsed.text.encode("utf-8")).hexdigest(),
        parents=[],
        chunks=[],
    )
    located = locations(parsed, format)
    # 使用与原解析块一致的换行规则，兼容 LF、CRLF 和单独 CR。
    line_offsets = [0]
    for line in parsed.text.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    def emit(parent, kind, spans, headers=None, language=None):
        text = "".join(parsed.text[a:b] for a, b in spans)
        if not text:
            return
        if len(text) > MAX_ATOMIC:
            raise ChunkFailure("STRUCTURE_TOO_LARGE", "不可切断的片段超过 8000 字符。")
        ordinal = len(result.chunks)
        positions = [
            SourceSpan(
                start=a,
                end=b,
                line_start=bisect_right(line_offsets, a) if format == "md" else None,
                line_end=bisect_right(line_offsets, b - 1) if format == "md" else None,
            )
            for a, b in spans
        ]
        result.chunks.append(
            ChildChunk(
                chunk_id=uuid5(set_id, f"child:{ordinal}"),
                parent_id=parent.parent_id,
                ordinal=ordinal,
                kind=kind,
                heading_path=parent.heading_path,
                text=text,
                spans=positions,
                page_number=parent.page_number,
                json_pointer=parent.json_pointer,
                table_headers=headers or [],
                code_language=language,
                atomic_oversize=len(text) > config.max_chars,
            )
        )
        if len(result.chunks) > MAX_CHUNKS:
            raise ChunkFailure("CHUNK_LIMIT_EXCEEDED", "子块数量超过 3000，停止生成。")

    for index, (items, path, start, end) in enumerate(
        section_groups(located, format, len(parsed.text))
    ):
        if index >= MAX_CHUNKS:
            raise ChunkFailure("CHUNK_LIMIT_EXCEEDED", "父块数量超过 3000，停止生成。")
        first = items[0][0]
        parent = ParentChunk(
            parent_id=uuid5(set_id, f"parent:{index}"),
            heading_path=path,
            start=start,
            end=end,
            text=parsed.text[start:end],
            page_number=first.page_number,
            json_pointer=first.json_pointer,
        )
        result.parents.append(parent)
        pending = []

        def flush():
            if pending:
                for spans in windows(pending[0][1], pending[-1][2], config):
                    emit(parent, "prose", spans)
                pending.clear()

        for block, block_start, block_end in items:
            if block.kind in ("paragraph", "other"):
                if pending and block_end - pending[0][1] > config.max_chars:
                    flush()
                pending.append((block, block_start, block_end))
            else:
                flush()
                if block.kind in ("table", "code"):
                    parts, headers, language = structured_parts(
                        parsed.text, block_start, block_end, block.kind, config
                    )
                    for spans in parts:
                        emit(parent, block.kind, spans, headers, language)
                else:
                    for spans in windows(block_start, block_end, config):
                        emit(parent, block.kind, spans)
        flush()
    result.snapshot_sha256 = snapshot_digest(result)
    return result
