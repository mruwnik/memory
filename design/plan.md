# Personal Multimodal Knowledge-Base — Design Document v1.0  
*(self-hosted, privacy-first)*  

---

## 1  Purpose

Build a single private system that lets **one user** ask natural-language questions (text or image) and instantly search:

* E-mail (IMAP or local mbox)  
* Slack & Discord messages  
* Git commit history  
* Photos (≈ 200 GB)  
* Books (EPUB/PDF), important blog posts / RSS items  
* Misc. documents & meeting transcripts  

…and receive answers that combine the most relevant passages, pictures and commits.

---

## 2  High-level Architecture

```
              ┌──────────────────────────┐
   Internet → │  Ingestion Workers       │
              │  (Celery queues)         │
              │  mail / chat / git / ... │
              └─────┬──────────┬─────────┘
                    │TEXT      │IMAGE/ATT
                    ▼          ▼
              ┌──────────┐   ┌──────────┐
              │Embedding  │   │OCR /     │
              │Workers    │   │Vision     │
              └────┬──────┘   └────┬─────┘
                   │ vectors       │ captions/tags
                   ▼               ▼
   Postgres 15  (canonical)   ◄───►  Qdrant 1.9 (vectors)
   • raw bodies / metadata         • per-modality collections
   • tags[] array, GIN index       • payload filter inde# Personal Multimodal Knowledge-Base — **Design Document v1.1**

*(self-hosted, privacy-first; incorporates external feedback except the “LLaVA-speed” concern, which is intentionally ignored)*  

---

## 1  Purpose

Provide a **single-user** system that answers natural-language questions about the owner’s entire digital corpus—e-mails, chats, code history, photos, books, blog posts, RSS items and ad-hoc documents—while keeping all data fully under personal control.

---

## 2  Target Workload & Service-Levels

| Metric | Year-1 | 5-year |
|--------|--------|--------|
| Text artefacts | ≈ 5 M | ≈ 25 M |
| Photos | ≈ 200 k (≈ 200 GB) | ≈ 600 k (≈ 600 GB) |
| Concurrency | 1 interactive seat + background jobs |
| **p95 answer latency** | ≤ 2 s (GPT-4o) |
| Uptime goal | “Home-lab” best-effort, but automatic recovery from single-component failures |

---

## 3  Hardware Specification  ‼ BREAKING

| Component | Spec | Notes |
|-----------|------|-------|
| CPU | 8-core / 16-thread (NUC 13 Pro i7 or similar) |
| **RAM** | **32 GB ECC** |
| **GPU** | **Low-profile RTX A2000 (6 GB)** — accelerates CLIP & local LLaVA |
| Storage | 2 TB NVMe (data) + 2 TB SATA SSD (offline backup/ZFS snapshot target) |
| Power | ≈ 10 W idle, 55 W peak |

---

## 4  Software Stack

| Layer | Tech |
|-------|------|
| OS | Ubuntu 22.04 LTS (automatic security updates) |
| Container runtime | Docker 24 + docker-compose v2 |
| **Message broker** | RabbitMQ 3.13 (priority queues, DLQ) |
| Database | PostgreSQL 15 |
| Vector DB | Qdrant 1.9 |
| Task engine | Celery 5 (broker = RabbitMQ, result-backend = Postgres) |
| Web/API | FastAPI + Uvicorn |
| Back-end LLMs | GPT-4o (API) **and** optional on-device LLaVA-1.6-Q4 (GPU) |
| Embeddings | OpenAI *text-embedding-3-small* (1536 d) • OpenCLIP ViT-B/32 (512 d) |

---

## 5  Data Sources & Ingestion Queues

| Source | Trigger | Parser | Default **tags[]** |
|--------|---------|--------|--------------------|
| **E-mail IMAP** | UID poll 10 min | `imap_tools` | `work` if address ends `@corp.com` |
| **Slack** | Socket-mode WS | `slack_sdk` | `work` on `#proj-*` |
| **Discord** | Gateway WS | `discord.py` | `personal` |
| **Git** | `post-receive` hook / hourly fetch | `GitPython` + LLM diff summary | `work` if remote host in allow-list |
| **Photos** | `watchdog` folder | `Pillow`, EXIF; CLIP embed; FaceNet | `personal` unless GPS in office polygon |
| **Books (EPUB/PDF)** | Nightly folder scan | `ebooklib`, `pdfminer`, OCR | `reference` |
| **Blog / RSS** | `feedparser` 30 min | `trafilatura` | `reference` |
| **Misc docs / transcripts** | `watchdog` inbox | PDF→OCR, DOCX→txt, VTT→txt | deduced from path |

---

## 6  Data Model

### 6.1 PostgreSQL (tables share columns)

```sql
id            bigserial primary key,
sha256        bytea unique,
inserted_at   timestamptz default now(),
tags          text[]      not null default '{}',  -- flexible labelling
lang          text,                               -- detected language
body_raw      text,                               -- TOAST/LZ4
vector_ids    text[],                             -- 0-N vectors in Qdrant
model_hash    text                                -- hash of embedding model
```

*GIN index on `tags`; range or JSONB indexes where relevant.*

### 6.2 Qdrant (collections)

| Collection | Model | Dim |
|------------|-------|-----|
| `mail`, `chat`, `git`, `book`, `blog`, `doc` | *text-embedding-3-small* | 1536 |
| `photo` | OpenCLIP ViT-B/32 | 512 |

Payload fields: `tags`, per-domain metadata (EXIF, author, files_changed[] …).

---

## 7  Task Queues & Concurrency

| Celery queue | Priority | Concurrency | Typical load |
|--------------|----------|-------------|--------------|
| `interactive` | 9 | auto (1 per core) | query embedding + GPT-4o calls |
| `medium_embed` | 5 | 4 | mail/chat embeddings |
| `low_ocr` | 2 | **≤ physical cores – 2** | PDF/image OCR |
| `photo_embed_gpu` | 5 | GPU | CLIP image vectors |
| `git_summary` | 4 | 2 | LLM diff summaries |
| All queues have DLQ → `failed_tasks` exchange (RabbitMQ). |

---

## 8  Vector Consistency & Repair

* **Up-front write:** worker inserts into Postgres, then Qdrant; the returned `vector_id` is stored in `vector_ids[]`.  
* **Audit Cron (5 min):**  
  * Find rows where `vector_ids = '{}'` or with `model_hash ≠ CURRENT_HASH`.  
  * Re-enqueue to appropriate embed queue.  
* **Qdrant-centric diff (hourly):** dump collection IDs → compare against Postgres; orphans are deleted, missing vectors are re-enqueued.  
* **Disaster Re-build:** documented script streams `id,chunk_text` to embed queue (rate-limited).

---

## 9  Embedding-Model Versioning

* Compute `MODEL_HASH = sha256(model_name + version + weights_SHA)` at worker start.  
* Model change → hashes differ → audit cron flags rows → background re-embed queue.  
* Router refuses to mix hashes unless `ALLOW_MIXED_MODE=1`.

---

## 10  Security Hardening

1. **JWT auth** on all API routes (HS256 secret in Docker secret store).  
2. **Rate limiter** (`slowapi`): 60 req / min / IP.  
3. **Filesystem isolation**  
   * Containers run as UID 1000, read-only bind mounts for `/photos`, `/books`.  
4. **TLS everywhere** (Traefik + Let’s Encrypt on LAN or Tailscale certs).  
5. **Input sanitisation**: Markdown-escape bodies; regex filter for SSNs/credit-card patterns before LLM prompt.  
6. **Resource quotas** in compose (`mem_limit`, `pids_limit`).

---

## 11  Backup & Restore

| Layer | Tool | Frequency | Storage cost (Glacier DA) |
|-------|------|-----------|---------------------------|
| Postgres basebackup + WAL | `pgBackRest` | nightly | included in dataset |
| Qdrant | `qdrant-backup` tar of collection dir | nightly | vectors + graph ≈ 20 GB year-5 |
| Files / attachments | Restic dedup | nightly | 400 GB -> ~€1.2 / mo |
| **Grandfather-father-son** pruning (`7-4-6`). |

Restore script: ① create fresh volumes, ② `pgbackrest restore`, ③ `qdrant-restore`, ④ run audit cron to verify.

---

## 12  Monitoring & Alerting

* **Prometheus exporters**  
  * node-exporter, postgres-exporter, rabbitmq-exporter, qdrant-exporter, cadvisor.  
* **Grafana dashboards**: CPU, RAM, queue depth, DLQ count, GPT-4o latency.  
* **Alertmanager rules**  
  * `vector_audit_missing > 500` → warn  
  * `node_filesystem_free_percent < 15` → critical  
  * `rabbitmq_queue_messages{queue="failed_tasks"} > 0` → critical  
  * `pg_up == 0` → critical  

---

## 13  Query Flow

1. **Embed** user text with *text-embedding-3* and CLIP-text (one call each).  
2. **Determine scope** from conversation memory (`tags = 'work'` etc.).  
3. **Async search** each relevant collection (max 3 per batch) with payload filter.  
4. **Merge** top-k by score.  
5. Build **evidence JSON** (snippets, thumbnails, commit summaries).  
6. **LLM**  
   * default: GPT-4o (vision) via API  
   * offline mode: local LLaVA-1.6 Q4 on GPU  
7. Stream answer + thumbnails.  
*Expected p95 latency on spec hardware: **~2 s** (cloud) | **~1.4 s** (local LLaVA).*

---

## 14  Internationalisation

* Tesseract language packs specified via `OCR_LANGS=eng+pol+deu`.  
* `langdetect` sets `lang` column; router boosts same-language chunks.  

---

## 15  Road-map

| Phase | Milestones |
|-------|------------|
| **0** | Hardware build, RabbitMQ, base compose up |
| **1** | Mail, chat, photo, git ingestion & audit loop |
| **2** | Backup scripts, security hardening, monitoring |
| **3** | Books/RSS/misc docs + international OCR |
| **4** | Tag-based multi-user RLS (optional) |

---

*End of Design Document v1.1*x
   • LISTEN/NOTIFY queue
          ▲                                  ▲
          │                                  │
          └────────────┬─────────────────────┘
                       │
                 FastAPI “/chat”
                 (router + merge)
                 + LangChain agent
                 + GPT-4o or local LLaVA
```

*Everything runs in Docker-Compose on a low-power x86 mini-PC (NUC 11/12; 16 GB RAM, 1 TB NVMe).*  

---

## 3  Data Sources & Ingestion

| Source | Trigger | Parser / Notes | Stored **tags** (default rules) |
|--------|---------|----------------|---------------------------------|
| **E-mail** (IMAP, mbox) | UID poll 10 min | `imap_tools`, strip quotes | `work` if address ends “@corp.com” |
| **Slack** | Socket-mode WS | `slack_sdk`, flatten blocks | `work` if channel `#proj-*` |
| **Discord** | Gateway WS | `discord.py`, role IDs | `personal` else |
| **Git commits** | `post-receive` hook or hourly fetch | `GitPython` → diff; 3-sentence summary via LLM | `work` if remote in `github.com/corp` |
| **Photos** | `watchdog` on folder | `Pillow`, EXIF; CLIP embed; FaceNet & optional YOLO tagger | `personal` unless GPS inside office |
| **Books** (EPUB/PDF) | Nightly scan of `/books` | `ebooklib` / `pdfminer` (+OCR) | `reference` |
| **Blog / RSS** | `feedparser` every 30 min | `trafilatura` HTML clean | `reference` |
| **Misc. docs / transcripts** | `watchdog` on `/kb-inbox` | PDF->OCR, DOCX→txt, VTT/SRT stitch | inferred from path (`/work/` etc.) |

---

## 4  Storage Model

### 4.1 PostgreSQL (system-of-record)

* Base tables: `mail_msg`, `chat_msg`, `git_commit`, `photo`, `book_doc`, `blog_post`, `misc_doc`, `attachment`.  
* Common columns: `id bigserial`, `sha256 bytea`, `inserted_at timestamptz`, `tags text[] NOT NULL DEFAULT '{}'`, `vector_ids text[]`.  
* All large bodies are **TOAST/LZ4** compressed; photos/attachments > 5 MB stay on disk with a path pointer.  
* GIN indexes on `tags` for millisecond filtering.  
* LISTEN/NOTIFY drives Celery (no Redis needed, but Redis used by default).

### 4.2 Qdrant (similarity index)

| Collection | Model | Dim | Distance | Extra payload |
|------------|-------|-----|----------|---------------|
| `mail`     | `text-embedding-3-small` | 1536 | Cosine | `tags`, `folder`, `from` |
| `chat`     | same | 1536 | Cosine | `channel_id`, `platform` |
| `git`      | same | 1536 | Cosine | `files_changed[]`, `author`, `tags` |
| `photo`    | OpenCLIP ViT-B/32 | 512 | Cosine | `exif_date`, `face_id`, `tags` |
| `book`, `blog`, `doc` | same | 1536 | Cosine | `title`, `source_url`, `tags` |

---

## 5  Workers & Queues

| Queue | Concurrency | Task | Key libs |
|-------|-------------|------|----------|
| `text` | 4 CPU | Chunk + embed text | OpenAI Python SDK |
| `image` | 2 CPU / GPU | Embed photo (CLIP) | `open_clip_torch` |
| `ocr` | 8 CPU | OCR PDF/image | `ocrmypdf`, `tesseract-ocr` |
| `git` | 2 CPU | Diff-summary → embed | GPT-4o mini or Φ-3-mini |
| `rss` | 1 CPU | Fetch feed, parse article | `feedparser`, `trafilatura` |
| `docs` | 2 CPU | Misc file parsing | `pdfminer`, `python-docx` |

Every queue auto-retries 3× with exponential back-off.

---

## 6  Tagging Framework

* YAML rule file; fields `sender_regex`, `path_regex`, `channel_regex`, `gps_polygon`, `add_tags[]`.  
* Workers call `apply_tags()` before inserting into Postgres/Qdrant.  
* CLI utility `retag add/remove <tag> (--where …)` for bulk fixes.  
* Tags are free-form strings; new tags require **no schema or index change** — Qdrant builds bitmap on first use.

---

## 7  Query & Chat Flow

1. **Router** embeds user text with  
   * CLIP-text → hits `photo`  
   * text-embed-3 → hits all text collections.  
2. Applies user-or conversation-scoped filter, e.g. `{"tags":{"value":"work"}}`.  
3. Parallel search (async) → merge top-k by score.  
4. Build “evidence bundle” (snippets, thumbs, commit msgs).  
5. Feed bundle + question to LLM:  
   * cloud GPT-4o (vision) **or**  
   * local LLaVA-1.6 + captions.  
6. Stream answer & thumbnails back.

Expected latency (NUC, GPT-4o): **≈ 1.3 s p95**.

---

## 8  Back-ups & DR

| Layer | Method | Retention | Cost |
|-------|--------|-----------|------|
| NVMe dataset | Restic dedup ⇒ **S3 Glacier Deep Archive** | 7 daily / 4 weekly / 6 monthly | First snapshot 250 GB → €0.9 / mo; delta ≈ €0.02 / mo |
| Local roll-back | ZFS hourly snapshots (compressed) | 7 days | disk-only |
| Restore test | Quarterly scripted restore to `/tmp/restore-test` | — | — |

---

## 9  Security

* Full-disk LUKS; if on AWS use encrypted EBS + **customer-managed KMS key**.  
* Instance in private subnet; access via Tailscale SSH or AWS SSM.  
* Docker containers run as non-root; seccomp default profile.  
* TLS termination in Traefik with auto-renewing Let’s Encrypt cert on LAN.  

---

## 10  Hardware & Performance

| Component | Spec | Head-room |
|-----------|------|-----------|
| Mini-PC | 4-core i5 (11th gen) / 16 GB RAM | p95 memory < 9 GB |
| Storage | 1 TB NVMe + ext. 1 TB SATA for backups | 5-year growth ≤ 400 GB |
| Power | 6 W idle → €1.5 / mo | — |
| GPU (optional) | Used RTX 2060 / T600 | Speeds 1st photo embed to < 1 h |

---

## 11  LLM & Model Abstraction

```python
class EmbedProvider(ABC):
    def embed(self, text: str) -> list[float]: ...

provider = OpenAIProvider(model="text-embedding-3-small")
# swap later:
# provider = OllamaProvider(model="nomic-embed-text")

# injection via environment
EMBED_BACKEND="openai"  # or "ollama"
```

Same interface for diff-summariser and chat-LLM; switching is one `docker-compose.yml` env var.

---

## 12  Monitoring & Ops

* **Prometheus + Grafana**: node load, Postgres WAL lag, queue depth.  
* **Watchtower** auto-updates images weekly (except Postgres & Qdrant).  
* Alertmanager e-mails if free disk < 15 % or any Celery worker dies.  

---

## 13  Roadmap / Open Items

| Phase | Deliverable |
|-------|-------------|
| **0** (done) | Design document v1.0 |
| **1** | Dockerfiles & compose stack; mail + chat + photo ingestion |
| **2** | Git summariser + OCR worker; tag rules config |
| **3** | Books, RSS, misc docs workers |
| **4** | Live chat UI & LLaVA offline option |
| **5** | Multi-user RLS & optional code-search add-on |

---

*Document ends — save for future implementation.*