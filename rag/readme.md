# rag 目录说明

本文档说明当前 `rag/` 目录中各脚本的职责、数据流和主要配置。ALI 邮件审阅系统的整体架构约束仍以 `ali/README.md` 为准；这里仅说明 retrieval / ingestion / FAISS 相关模块。

## 当前数据流

### Standard 文档流程

当前 standard 文档采用分步处理流程：

1. `standard_1_pdf_to_txt.py`
   从 `data/standard/pdf/*.pdf` 抽取文本，输出到 `data/standard/txt_raw/*.txt`。

2. `standard_2_txt_to_sanitized.py`
   清理 TXT 中的 overlay/header/footer 噪声，并插入 page break 标记，输出到 `data/standard/txt_splitted/*.page_splited`。

3. `standard_3_sanitized_to_jsonl.py`
   将 split 后的 TXT 转成 page-scoped JSONL，输出到 `data/standard/jsonl/*_chunks.jsonl`。

4. `standard_4_bundle_jsonl.py`
   合并 `data/standard/jsonl/*_chunks.jsonl`，输出 `data/standard/jsonl/standard_chunks.jsonl`。

5. `faiss_build.py`
   读取 `data/<TARGET>/jsonl/*_chunks.jsonl`，构建 FAISS index 和 SQLite metadata。目前 `TARGET = "standard"`。

### Mbox 邮件流程

当前 mbox 邮件流程：

1. `imap_to_mbox_all_folder.py`
   从 IMAP 拉取邮件，保存到 `data/mbox/raw/`。

2. `parse_mbox_to_chunk.py`
   读取 `data/mbox/raw/` 中的 mbox 文件，解析 email body 和支持的附件，输出：
   - `data/mbox/jsonl/<mbox_stem>_blocks.jsonl`
   - `data/mbox/jsonl/<mbox_stem>_chunks.jsonl`
   - 可选 audit sidecar：`*_drop.jsonl`、`*_split_added.jsonl`

3. `faiss_build.py`
   如需构建 mbox FAISS，需要将 `faiss_build.py` 中的 `TARGET` 改为 `"mbox"`，并确认目标 JSONL 位于 `data/mbox/jsonl/*_chunks.jsonl`。

## Runtime RAG

| 文件 | 说明 |
| --- | --- |
| `helper_rag_pipeline.py` | RAG 查询主流程。按 mode 载入 `data/faiss/<mode>_faiss.index` 和 `data/faiss/<mode>_metadata.sqlite`，对问题做 embedding，执行 FAISS 或 brute-force 检索，按分数过滤，组装 context，调用 LLM，并返回答案和相似度表。 |
| `helper_query_rewriting.py` | 可选 query rewrite 辅助模块。生成少量 retrieval query variants，并在多次检索后按 index 合并候选结果、保留最高分。当前 `helper_rag_pipeline.py` 中 `ENABLE_QUERY_REWRITE = False`，默认不生效。 |
| `helper_faiss_embedding.py` | BGE M3 embedding wrapper。加载本地/offline embedding 模型，生成 L2-normalized NumPy embedding，并复用进程内 singleton。 |

`helper_rag_pipeline.py` 主要配置：

| 配置 | 当前值 | 说明 |
| --- | ---: | --- |
| `SEARCH_BACKEND` | `"faiss"` | 检索后端，可选 `"faiss"` 或 `"brute"`。 |
| `TOP_K` | `10` | 最终送入 context 的结果数量上限。 |
| `CANDIDATE_K` | `50` | 初始候选检索数量。 |
| `SCORE_THRESHOLD` | `0.4` | 相似度过滤门槛；如果全部低于门槛，至少保留最高分一笔。 |
| `ENABLE_QUERY_REWRITE` | `False` | 是否启用 query variants 多路检索。 |
| `REWRITE_MAX_VARIANTS` | `3` | query rewrite 开启时最多生成的 variants 数。 |

## FAISS 构建

| 文件 | 说明 |
| --- | --- |
| `faiss_build.py` | FAISS 构建入口。扫描 `data/<TARGET>/jsonl/*_chunks.jsonl`，对每个 chunks JSONL 调用 `build_index()`。目前 `TARGET = "standard"`。 |
| `faiss_index_builder.py` | 从 chunks JSONL 构建 FAISS inner-product index 和 SQLite metadata store。SQLite 中保存 `chunk_text` 和除 `text` 外的 metadata；FAISS vector id 与 SQLite `vector_id` 对齐。 |

输出文件命名：

| 输入 | 输出 |
| --- | --- |
| `data/<target>/jsonl/<name>_chunks.jsonl` | `data/faiss/<name>_faiss.index` |
| `data/<target>/jsonl/<name>_chunks.jsonl` | `data/faiss/<name>_metadata.sqlite` |

## Standard 文档脚本

| 文件 | 说明 |
| --- | --- |
| `standard_1_pdf_to_txt.py` | 扫描 `data/standard/pdf`，用 `parse_pdf_to_raw.get_pdf_full_text()` 从 PDF 抽取文本，写入 `data/standard/txt_raw`。 |
| `standard_2_txt_to_sanitized.py` | 扫描 `data/standard/txt_raw`，调用 `helper_sanitize.clean_overlay()` 和 `apply_page_splitting()`，写入 `data/standard/txt_splitted`。 |
| `standard_3_sanitized_to_jsonl.py` | 将 `*.page_splited` 拆成 page-scoped JSONL block，注入 `UL {standard_number}, page {page}` 前缀，写入 `data/standard/jsonl/*_chunks.jsonl`。 |
| `standard_4_bundle_jsonl.py` | 合并 `data/standard/jsonl/*_chunks.jsonl`，生成 `data/standard/jsonl/standard_chunks.jsonl`。 |

## Mbox 邮件脚本

| 文件 | 说明 |
| --- | --- |
| `imap_to_mbox_all_folder.py` | IMAP 导出脚本。默认扫描所有可访问 folder，忽略 `Trash`、`Junk`，按日期范围拉取邮件并保存 mbox。账号从 `.env` 的 `IMAP_USERNAME`、`IMAP_PASSWORD` 读取。 |
| `parse_mbox_to_chunk.py` | mbox ingestion 主脚本。解析邮件正文，保存附件原始 payload，可选解析 PDF 附件，然后调用 `parse_block_to_chunk.build_chunks_jsonl()` 生成 `_chunks.jsonl`。当前附件 parser 只启用 `.pdf`；Word/Excel parser 代码保留但默认注释。 |

## Parsing 与 chunking 辅助模块

| 文件 | 说明 |
| --- | --- |
| `parse_raw_to_jsonl.py` | canonical block 转换层。统一 email、PDF、Word、Excel 的 raw blocks 为包含 `page`、`char`、`word`、`part`、`text` 等字段的 block stream。 |
| `parse_block_to_chunk.py` | 将 block JSONL 转成 retrieval chunks。会过滤过短/低信息量 block，拆分过长 block，并写出 chunks JSONL 与可选 audit 文件。 |
| `parse_email_to_raw.py` | 邮件正文 parser。使用 quote-depth 逻辑拆分正文，并做基本文本清理。 |
| `parse_pdf_to_raw.py` | PDF 文本抽取。基于 PyMuPDF 抽取页面文本，并在需要时使用 OCR fallback。 |
| `parse_doc_to_raw.py` | Word 文件 raw block wrapper。将 `.doc` / `.docx` 文本抽成 paragraph-level raw blocks。 |
| `parse_doc_helper.py` | Word 底层抽取 helper。`.docx` 原生读取，legacy `.doc` 尝试 `antiword`、`catdoc`、`soffice/libreoffice`。 |
| `parse_xls.py` | Excel 抽取 helper。读取 XLS/XLSX，按 sheet 抽取并清理文本，支持并行处理失败后的顺序 fallback。 |
| `helper_sanitize.py` | 文本清理工具。提供通用 whitespace/字符清理、标准文档 overlay 移除、page break 插入等函数。 |

## 备注

- 当前 `rag/` 目录没有 `test_*.py` 测试脚本；旧 README 中的测试脚本说明已移除。
- 当前 `rag/` 目录没有 `imap_to_mbox.py`；IMAP 导出入口是 `imap_to_mbox_all_folder.py`。
- Standard 文档目前以 `standard_1` 到 `standard_4` 的分步流程为准，不再记录旧的一步式 `parse_standard_to_block.py` 流程。
