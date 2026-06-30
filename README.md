# bzCard

bzCard is a self-hosted business card database for Japanese business cards.

It accepts JPEG/PNG business card images, stores the original and processed images,
runs OCR with `yomitoku`, extracts structured fields with a local LLM through
Ollama, and provides both a WebUI and LINE official account integration.

This is a personal-use first implementation. Do not expose it publicly without
putting it behind your own authentication and HTTPS reverse proxy.

## Features

- Upload business card images from WebUI
- Register card images from a LINE official account webhook
- Store original image, processed image, and thumbnail
- Auto crop, perspective correction, brightness/contrast/sharpness correction
- Auto retry image orientation when LINE/mobile images arrive rotated
- OCR with `yomitoku`
- Field extraction with a local Ollama model, default `qwen2.5:7b`
- Front/back image support
- Simple duplicate detection by image hash
- Simple bearer-token auth for WebUI/API
- LIFF-based LINE user registration with invite code
- SQLite storage
- `not_card` status for images that do not look like business cards

## Architecture

- `api`: FastAPI, SQLite, background worker, OCR/LLM processing
- `ui`: React + Vite, served by nginx
- `ollama`: local LLM runtime
- `data/`: persistent SQLite database and uploaded images

Default local ports:

- WebUI: `http://localhost:15174/bzcard/`
- API: `http://localhost:18081/`
- Ollama: `http://localhost:11434/`

The default deployment assumes a host nginx reverse proxy:

- WebUI subpath: `/bzcard/`
- API subpath: `/bzcard-api/`

## Repository Safety

This repository is intended to be public-safe.

The following must not be committed:

- `.env`
- `data/`
- uploaded card images
- SQLite database files
- LINE channel secrets or access tokens
- real invite codes
- real bearer tokens

`.gitignore` excludes those local files. Before committing, check:

```sh
git status --short
```

## Quick Start

Create `.env` from the sample values below.

```sh
cp .env.example .env
```

Edit at least `APP_API_TOKEN`.

Start services:

```sh
docker compose up --build -d
```

Pull the default Ollama model:

```sh
docker compose exec ollama ollama pull qwen2.5:7b
```

Open:

```text
http://localhost:15174/bzcard/
```

Use the bearer token configured in `APP_API_TOKEN`.

## Environment

`docker-compose.yml` reads local secrets from `.env`.

Required for normal WebUI/API use:

```env
APP_API_TOKEN=replace-with-a-long-random-token
```

Optional LINE integration:

```env
LINE_CHANNEL_SECRET=
LINE_CHANNEL_ACCESS_TOKEN=
LINE_LOGIN_CHANNEL_ID=
LINE_LIFF_ID=
LINE_LIFF_URL=
LINE_INVITE_CODES=
LINE_SESSION_TTL_HOURS=720
LINE_CARD_SCOPE=personal
```

Other defaults:

```env
LLM_MODEL=qwen2.5:7b
MAX_UPLOAD_MB=50
```

## Reverse Proxy Example

Use a host nginx or similar reverse proxy. The API needs a larger request body
limit for image uploads.

```nginx
location /bzcard-api/ {
    client_max_body_size 60m;
    proxy_pass http://127.0.0.1:18081/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /bzcard/ {
    proxy_pass http://127.0.0.1:15174/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## API Example

```sh
curl -H "Authorization: Bearer ${APP_API_TOKEN}" \
  -F "file=@sample.jpg" \
  "http://localhost:18081/api/cards/upload?direction=auto"
```

`direction` can be:

- `auto`
- `horizontal`
- `vertical`

## LINE Integration

LINE webhook URL:

```text
https://your-domain.example/bzcard-api/line/webhook
```

LIFF endpoint URL:

```text
https://your-domain.example/bzcard/liff
```

Required LINE settings:

- `LINE_CHANNEL_SECRET`: Messaging API channel secret
- `LINE_CHANNEL_ACCESS_TOKEN`: Messaging API channel access token
- `LINE_LOGIN_CHANNEL_ID`: LINE Login channel ID
- `LINE_LIFF_ID`: LIFF ID
- `LINE_LIFF_URL`: for example `https://liff.line.me/LIFF_ID`
- `LINE_INVITE_CODES`: comma-separated invite codes

LINE flow:

1. User opens LIFF registration screen.
2. LIFF gets LINE ID token.
3. API verifies the ID token with LINE.
4. User enters an invite code.
5. Active users can send card images to the official account.
6. Webhook registers the image into the same processing queue as WebUI uploads.

`LINE_CARD_SCOPE`:

- `personal`: active LINE users can access all cards. Best for single-user use.
- `owner`: users can access only cards registered with their LINE user ID.

## Processing Pipeline

1. Store uploaded original image.
2. Create processed image:
   - EXIF transpose
   - auto crop
   - perspective correction
   - brightness/contrast/sharpness correction
3. Run `yomitoku` OCR.
4. If `direction=auto`, infer horizontal/vertical text order.
5. If a mobile/LINE image looks rotated, test 90-degree candidates and fix
   processed image orientation when OCR confirms it.
6. Reject obvious non-card images as `not_card`.
7. Send OCR text to Ollama LLM for JSON field extraction.
8. Store extracted fields in SQLite.

## Memory Limits

The compose file includes conservative container limits for a roughly 12 GB host:

- `api`: `3g`
- `ui`: `128m`
- `ollama`: `6g`

Adjust these based on actual `docker stats` during OCR/LLM processing.

## Development Checks

Compile API sources:

```sh
python3 -m compileall api/src
```

Build containers:

```sh
docker compose build api ui
```

Check API health:

```sh
docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/ping').status)"
```

## Notes

- OCR quality mainly depends on `yomitoku` and image quality.
- The LLM is used for field extraction, not for image OCR itself.
- Low-resolution migrated card images will usually produce poor OCR results.
- For public deployment, always use HTTPS and external access controls.
