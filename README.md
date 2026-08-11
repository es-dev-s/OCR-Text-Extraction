# PDF Extract API

Async FastAPI service that extracts **embedded PDF text** (no OCR) and detects the **document title** from page 1 → page 2 for near-zero latency.

Use this from your **Next.js** platform after hosting on Railway.

---

## What it does

| Goal | Endpoint | Latency |
|------|----------|---------|
| Detect PDF title only | `POST /v1/title` | Very fast (~50ms on Demo.pdf) — scans at most 2 pages |
| Extract all text + per-page titles | `POST /v1/extract` | Depends on page count (~2s for 20 pages) |
| Many titles at once | `POST /v1/batch/title` | Concurrent, max 20 files |
| Many full extracts at once | `POST /v1/batch/extract` | Concurrent, max 12 files |

**Title rules (first-wins):**
1. Peek page 1 — if it has a title, that is the PDF title (stop).
2. If page 1 is empty **or** has no title → peek page 2.
3. If page 2 has a title → use that.
4. Else fall back to PDF metadata `title` (if present).

---

## Environment variables

Create `.env` locally (already provided). Set the **same keys** in Railway → **Variables**.

| Variable | Required | Example | Description |
|----------|----------|---------|-------------|
| `API_KEY` | Yes (prod) | `dev-pdf-extract-key-change-me` | Shared secret. Send as `X-API-Key` header. |
| `CORS_ORIGINS` | Recommended | `http://localhost:3000,https://your-app.vercel.app` | Allowed browser origins (comma-separated). |
| `MAX_UPLOAD_MB` | No | `25` | Max PDF size per upload. |
| `MAX_CONCURRENT_JOBS` | No | `8` | Max parallel PDF jobs. |
| `TITLE_MAX_PAGES` | No | `2` | Early pages to peek for document title. |
| `PORT` | Auto on Railway | `8000` | Local bind port. Railway sets `PORT` for you. |
| `PDF_API_URL` | Optional | `http://127.0.0.1:8000` | Documented base URL of this API. |

### Local `.env`

```bash
cp .env.example .env
# edit API_KEY / CORS_ORIGINS
```

### Railway

In the Railway service → **Variables**, add:

```
API_KEY=your-long-random-secret
CORS_ORIGINS=https://your-nextjs-domain.com,http://localhost:3000
MAX_UPLOAD_MB=25
MAX_CONCURRENT_JOBS=8
TITLE_MAX_PAGES=2
```

Do **not** rely on committing `.env` to Railway — set variables in the dashboard. `.env` is gitignored.

---

## Run locally

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- Health: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- Swagger: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

## Auth (login for your platform)

There is no user login form. Your platform authenticates with a shared API key:

```http
X-API-Key: <API_KEY from .env / Railway>
```

- If `API_KEY` is set → required on all `/v1/*` routes.
- If `API_KEY` is empty → open (local debugging only).
- Keep the key **server-side** in Next.js (Route Handler / Server Action). Do **not** expose it as `NEXT_PUBLIC_*`.

---

## API reference

Base URL examples:

- Local: `http://127.0.0.1:8000`
- Railway: `https://<your-service>.up.railway.app`

All upload endpoints use `multipart/form-data`.

### 1) Health

```http
GET /health
```

Response:

```json
{
  "ok": true,
  "service": "pdf-extract-api",
  "uptime_s": 12.3,
  "active_jobs": 0,
  "max_concurrent_jobs": 8,
  "max_upload_mb": 25.0,
  "api_key_required": true
}
```

---

### 2) Extract title only (recommended default for uploads)

```http
POST /v1/title
Content-Type: multipart/form-data
X-API-Key: <API_KEY>
```

Form fields:

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `file` | file | yes | — | `.pdf` only |
| `title_max_pages` | int | no | `2` | Peek at most this many early pages |

Example `curl`:

```bash
curl -X POST "$PDF_API_URL/v1/title" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@./Demo.pdf;type=application/pdf"
```

Example success response:

```json
{
  "ok": true,
  "request_id": "fd78a77db411",
  "filename": "Demo.pdf",
  "document_title": "THE WATER OF SYSTEMS CHANGE",
  "document_title_source": "page_1_content",
  "document_title_confidence": 0.5,
  "document_title_page": 1,
  "pages_scanned_for_title": 1,
  "title_pages_checked": [
    {
      "page": 1,
      "is_empty": false,
      "title": "THE WATER OF SYSTEMS CHANGE",
      "confidence": 0.5,
      "source": "content_font_heuristic",
      "char_count": 129
    }
  ],
  "page_count": 20,
  "is_encrypted": false,
  "metadata": { "format": "PDF 1.4", "creator": "Adobe InDesign 14.0 (Windows)" },
  "elapsed_ms": 50.79
}
```

Fields your Next.js app usually needs:

- `document_title` — main title string (or `null`)
- `document_title_page` — which page won
- `document_title_confidence` — 0..1 heuristic confidence
- `pages_scanned_for_title` — 1 or 2 (never the whole PDF)
- `elapsed_ms` — server processing time

---

### 3) Extract all data

```http
POST /v1/extract
Content-Type: multipart/form-data
X-API-Key: <API_KEY>
```

Form fields:

| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `file` | file | yes | — | `.pdf` only |
| `include_pages` | bool | no | `true` | Include per-page text/tables/links/images |
| `include_full_text` | bool | no | `false` | Include concatenated `full_text` |
| `title_max_pages` | int | no | `2` | Early pages for document title |

Example `curl`:

```bash
curl -X POST "$PDF_API_URL/v1/extract" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@./Demo.pdf;type=application/pdf" \
  -F "include_pages=true" \
  -F "include_full_text=false"
```

Example success response (trimmed):

```json
{
  "ok": true,
  "request_id": "a1b2c3d4e5f6",
  "filename": "Demo.pdf",
  "document_title": "THE WATER OF SYSTEMS CHANGE",
  "document_title_source": "page_1_content",
  "document_title_confidence": 0.5,
  "document_title_page": 1,
  "pages_scanned_for_title": 1,
  "page_count": 20,
  "page_titles": [
    { "page": 1, "title": "THE WATER OF SYSTEMS CHANGE", "confidence": 0.5, "headings": ["THE WATER OF SYSTEMS CHANGE"] },
    { "page": 2, "title": null, "confidence": 0.0, "headings": [] },
    { "page": 3, "title": "Six Conditions of Systems Change", "confidence": 0.643, "headings": ["Six Conditions of Systems Change"] }
  ],
  "pages": [
    {
      "page": 1,
      "page_title": "THE WATER OF SYSTEMS CHANGE",
      "page_title_confidence": 0.5,
      "headings": [{ "text": "THE WATER OF SYSTEMS CHANGE", "score": 6.0, "font_size": 30.0, "bold": false, "y": 548.2 }],
      "text": "THE WATER OF SYSTEMS CHANGE\n...",
      "char_count": 129,
      "tables": [],
      "links": [],
      "annotations": [],
      "images": [{ "xref": 640, "width": 2843, "height": 3379 }]
    }
  ],
  "elapsed_ms": 2193.62
}
```

Tip for platform UX:

1. On upload → call `/v1/title` immediately (show title in UI).
2. In background → call `/v1/extract` for full indexing / search.

---

### 4) Batch endpoints

**Titles**

```http
POST /v1/batch/title
```

- Form field name: `files` (repeat for each PDF)
- Max 20 files

**Full extract**

```http
POST /v1/batch/extract
```

- Form field name: `files`
- Max 12 files
- Same optional flags as `/v1/extract`

Batch response shape:

```json
{
  "ok": true,
  "total": 3,
  "succeeded": 3,
  "failed": 0,
  "elapsed_ms": 40.82,
  "results": [ { "ok": true, "document_title": "..." }, { "ok": true } ]
}
```

Failed items return `ok: false` with `error` and do not fail the whole batch.

---

## Next.js integration

### A. Env in Next.js

`.env.local` (server only):

```bash
PDF_API_URL=http://127.0.0.1:8000
# after Railway deploy:
# PDF_API_URL=https://your-pdf-api.up.railway.app

PDF_API_KEY=dev-pdf-extract-key-change-me
```

Never prefix the API key with `NEXT_PUBLIC_`.

---

### B. Recommended: Next.js Route Handler (keeps API key secret)

`app/api/pdf/title/route.ts`

```ts
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const form = await req.formData();
  const file = form.get("file");

  if (!(file instanceof File)) {
    return NextResponse.json({ ok: false, error: "file is required" }, { status: 400 });
  }

  const outbound = new FormData();
  outbound.append("file", file, file.name);

  const res = await fetch(`${process.env.PDF_API_URL}/v1/title`, {
    method: "POST",
    headers: {
      "X-API-Key": process.env.PDF_API_KEY ?? "",
    },
    body: outbound,
  });

  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
```

`app/api/pdf/extract/route.ts`

```ts
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const form = await req.formData();
  const file = form.get("file");

  if (!(file instanceof File)) {
    return NextResponse.json({ ok: false, error: "file is required" }, { status: 400 });
  }

  const outbound = new FormData();
  outbound.append("file", file, file.name);
  outbound.append("include_pages", "true");
  outbound.append("include_full_text", "false");

  const res = await fetch(`${process.env.PDF_API_URL}/v1/extract`, {
    method: "POST",
    headers: {
      "X-API-Key": process.env.PDF_API_KEY ?? "",
    },
    body: outbound,
  });

  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
```

---

### C. Client upload component (calls your Next.js routes)

```tsx
"use client";

export function PdfUploader() {
  async function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    const body = new FormData();
    body.append("file", file);

    // 1) Fast title
    const titleRes = await fetch("/api/pdf/title", { method: "POST", body });
    const titleJson = await titleRes.json();
    console.log("title", titleJson.document_title);

    // 2) Full extract (reuse a new FormData; body streams are one-shot)
    const body2 = new FormData();
    body2.append("file", file);
    const extractRes = await fetch("/api/pdf/extract", { method: "POST", body: body2 });
    const extractJson = await extractRes.json();
    console.log("pages", extractJson.page_count, extractJson.page_titles);
  }

  return <input type="file" accept="application/pdf" onChange={onChange} />;
}
```

---

### D. TypeScript types (copy into your Next.js app)

```ts
export type TitlePagesChecked = {
  page: number;
  is_empty: boolean;
  title: string | null;
  confidence: number;
  source: string;
  char_count: number;
};

export type TitleResponse = {
  ok: boolean;
  request_id: string;
  filename: string;
  document_title: string | null;
  document_title_source: string;
  document_title_confidence: number;
  document_title_page: number | null;
  pages_scanned_for_title: number;
  title_pages_checked: TitlePagesChecked[];
  page_count: number;
  is_encrypted: boolean;
  metadata: Record<string, string>;
  elapsed_ms: number;
  error?: string;
};

export type PageTitleSummary = {
  page: number;
  title: string | null;
  confidence: number;
  headings: string[];
};

export type ExtractPage = {
  page: number;
  page_title: string | null;
  page_title_confidence: number;
  headings: Array<{
    text: string;
    score: number;
    font_size: number;
    bold: boolean;
    y: number;
  }>;
  text: string;
  char_count: number;
  tables: unknown[];
  links: unknown[];
  annotations: unknown[];
  images: unknown[];
};

export type ExtractResponse = {
  ok: boolean;
  request_id: string;
  filename: string;
  document_title: string | null;
  document_title_source: string;
  document_title_confidence: number;
  document_title_page: number | null;
  pages_scanned_for_title: number;
  page_count: number;
  page_titles: PageTitleSummary[];
  pages?: ExtractPage[];
  full_text?: string;
  elapsed_ms: number;
  error?: string;
};
```

---

## Error codes

| Status | Meaning |
|--------|---------|
| `400` | Missing/invalid file, wrong type, empty upload |
| `401` | Missing/invalid `X-API-Key` |
| `413` | PDF larger than `MAX_UPLOAD_MB` |
| `422` | PDF could not be parsed / extraction failed |

---

## Tests (run before deploy)

```bash
source venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

This suite covers:
- unit title detection (first-wins, empty-page fallback, thesis cover noise)
- API health / auth / title / extract / batch
- real sample PDFs when present locally (not committed)

---

## Deploy on Railway

1. Push this repo and create a Railway service from it (Dockerfile is used via `railway.toml`).
2. Set Variables (same keys as `.env.example`):

```
API_KEY=your-long-random-secret
CORS_ORIGINS=https://your-nextjs-domain.com,http://localhost:3000
MAX_UPLOAD_MB=25
MAX_CONCURRENT_JOBS=8
TITLE_MAX_PAGES=2
```

3. **Important:** In Railway → Service → Settings → Deploy, clear any custom
   **Start Command**. Let the Dockerfile `CMD ["python", "run.py"]` run.
   Do **not** set something like `uvicorn ... --port ${PORT:-8000}` — Railway
   will pass that literally and crash with `Invalid value for '--port'`.
4. Deploy → copy public URL into Next.js `PDF_API_URL`.
5. Healthcheck path is `/health` (configured in `railway.toml`).

Local smoke test against Railway:

```bash
export PDF_API_URL=https://your-service.up.railway.app
export API_KEY=your-long-random-secret

curl -s "$PDF_API_URL/health"

curl -s -X POST "$PDF_API_URL/v1/title" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@./Demo.pdf;type=application/pdf"
```

---

## CLI (optional, no HTTP)

```bash
# title only
python extract_pdf.py --title-only

# full extract JSON
python extract_pdf.py Demo.pdf -o extraction_result.json

# per-page titles summary
python extract_pdf.py --titles
```

---

## Notes

- This extracts **embedded text only**. Scanned image-only PDFs need OCR (not included).
- Title detection never needs to read all 100 pages.
- For 10–12 uploads/hour, default `MAX_CONCURRENT_JOBS=8` is plenty and stays robust under bursts.
