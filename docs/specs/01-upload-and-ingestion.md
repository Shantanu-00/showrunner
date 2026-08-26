# Spec 01 — Upload & Ingestion

Goal: a guest selects 30 photos on hotel Wi-Fi, locks their phone, walks away — and every photo still arrives exactly once, with correct ownership and consent, even if they reopen the app hours later or on a different network.

## 1. Design principles

1. **Register intent before bytes.** The client tells the API what it intends to upload; Firestore media docs are created *first* (status `awaiting_upload`, consent + uploader already attached). Bytes arriving later just flip a status. This makes orphans detectable, consent unambiguous, and the pipeline metadata-complete before processing starts.
2. **Bytes never touch our servers.** Browser → GCS directly via signed URLs. GCS is the shock absorber for 500 concurrent uploaders.
3. **The client outbox is the source of upload truth.** Uploads survive app close because the queue lives in IndexedDB, not in JS memory.

## 2. Client upload manager (PWA)

### 2.1 Outbox (IndexedDB, `idb` library)

On file selection (multi-select or camera capture):

```
outbox/{clientMediaId}: {
  blob,                      // the File/Blob itself (IndexedDB stores Blobs fine)
  fileName, contentType, size, lastModified,
  eventId, batchId,          // batchId = ULID per selection action
  consent: { pool: true, public: bool, selfOnly: bool },   // captured at selection time (spec 02 §4)
  state: 'queued' | 'url_issued' | 'uploading' | 'done' | 'failed',
  signedUrl?, urlExpiresAt?, resumableSessionUri?, bytesSent?,
  attempts: 0
}
```

- Write ALL selected files to the outbox **synchronously before any network call**. UI renders from the outbox (chips: queued / uploading / done), so state is truthful across reloads.
- Upload loop: max **3 concurrent** transfers (mobile radio + venue Wi-Fi friendly). Exponential backoff per item (1s→2s→4s→…, max 5 attempts, then `failed` with manual retry button).
- **Resume triggers:** app open, `visibilitychange` → visible, `online` event, and a 15s interval while the app is open. Any of these drains the outbox. If the phone is locked mid-upload, in-flight PUTs die; on next open the loop resumes from the outbox. Nothing is lost because blobs persist.
- **Background Fetch API** (Chromium/Android only): progressive enhancement — if `'BackgroundFetchManager' in self`, hand large batches to the service worker so uploads continue after tab close. Feature-detect; iOS falls back to outbox-resume. Never build the demo on it.
- Cleanup: delete blob from outbox on `done` (keep a small `done` ledger of {clientMediaId, thumbDataUrl} for instant UI).
- Signed URLs expire (15 min): if `urlExpiresAt` passed, re-request a URL for that mediaId (API re-issues for same object path — idempotent).

### 2.2 Photos vs videos

- **Photos (≤ 20 MB): single signed PUT.** One request, atomic — a failure just retries the whole file. **Size is enforced, not trusted:** the declared `Content-Length` is included in the V4 signature's signed headers, so GCS rejects a PUT whose actual length differs — a malicious client cannot stream 5 GB through a URL issued for 4 MB. Intake additionally verifies object size ≤ cap (belt and braces) and deletes + rejects oversized objects.
- **Videos (≤ 200 MB, ≤ 60 s for demo): resumable session.** API initiates the GCS resumable session server-side (pinning `x-upload-content-length` at initiation so GCS enforces the declared size) and returns the **session URI** (it's a bearer token, valid 1 week — do not log it). Client uploads chunks in multiples of 256 KiB, persisting `bytesSent` in the outbox. On resume: `PUT` with `Content-Range: bytes */TOTAL` to query GCS for the committed offset, continue from there. `410 Gone` → restart session via API.
- Optional "data saver" toggle: client-side canvas re-encode of photos > 8 MB to ~2560px JPEG q85 before upload. Default OFF (people want originals).

## 3. API contracts (Cloud Run `api` service)

```
POST /v1/events/{eventId}/uploads          (auth: Firebase ID token, anonymous ok)
  body: { batchId, consent: {public, selfOnly},
          bountyId?,                                 // set when the batch answers a bounty banner (spec 05 §3)
          files: [{clientMediaId, fileName, contentType, size, capturedAt?}] }   // ≤ 50/call
  → 200: { uploads: [{ mediaId,                        // == clientMediaId (validated ULID)
                        kind: 'photo'|'video',
                        signedUrl? , resumableSessionUri?,
                        expiresAt }] }
  Side effects: creates events/{eventId}/media/{mediaId} docs:
    { uploaderUid, batchId, consent, kind, contentType, size, bountyId?,
      status: 'awaiting_upload', stages: {}, createdAt, capturedAt? }
  Validation: bountyId (if present) must reference an 'active' bounty in this event, else dropped silently.

POST /v1/events/{eventId}/uploads/{mediaId}/refresh-url    → new signed URL (same object path)
```

Validation: ULID format; contentType allowlist (`image/jpeg|png|webp|heic`, `video/mp4|quicktime`); size caps (enforced via signed `Content-Length`, see §2.2); per-uid rate limit (e.g. 300 files/hour) via Firestore counter; event must be `live` (uploads closed in `draft`/`paused`/`wrapped` — see spec 08); `refresh-url` and re-registration verify `uploaderUid == caller` (a guest can never obtain a URL for someone else's mediaId — 403).

**Signed URL details:** V4 signed PUT, 15-min expiry, `Content-Type` **and** `Content-Length` pinned in signed headers, on the **XML API endpoint** (JSON endpoints ignore CORS config). Bucket CORS: origins = app domains; methods PUT, POST, GET, HEAD; responseHeader includes `Content-Type`, `x-goog-resumable`; never list OPTIONS.

## 4. GCS layout

```
gs://{raw}/events/{eventId}/media/{mediaId}/original.{ext}
gs://{derived}/events/{eventId}/media/{mediaId}/thumb_384.webp      # gallery grid
gs://{derived}/events/{eventId}/media/{mediaId}/classify_768.webp   # what Gemini sees (258–1548 tokens)
gs://{derived}/events/{eventId}/media/{mediaId}/display_1600.webp   # lightbox / kiosk
gs://{derived}/events/{eventId}/media/{mediaId}/poster.jpg          # video poster frame
gs://{derived}/events/{eventId}/media/{mediaId}/proxy_720.mp4       # video playback proxy
gs://{curated}/events/{eventId}/reels/{reelId}/v{n}.mp4
```

Re-upload of the same `mediaId` overwrites the same object → naturally idempotent. Derived files go to a **separate bucket** so the finalize trigger never loops.

## 5. Intake (Eventarc → Cloud Run `intake` service)

Trigger: `google.cloud.storage.object.v1.finalized` on the raw bucket.

```
handler(cloudEvent):
  parse eventId, mediaId from object name; else → log + ack (ignore strays)
  doc = events/{eventId}/media/{mediaId}
  if doc missing → quarantine object (no registered intent), ack
  if object.size > kind cap → delete object, doc.status='rejected' (reason=oversize), ack
  txn: if doc.status not in ('awaiting_upload','uploaded'): ack (duplicate delivery — idempotent)
       set status='uploaded', gcsUri, objectGeneration, md5Hash (from GCS metadata, free), uploadedAt
  DEDUPE: txn create hashes/{md5} → if it already exists for this event:
       doc.duplicateOf = canonicalMediaId; copy canonical perception results when ready;
       skip perception tasks (duplicates never re-classified, never surface publicly — spec 04)
  photos: extract EXIF — capturedAt from DateTimeOriginal interpreted in event.timezone
          (EXIF carries NO timezone; see spec 03 §5.1); missing/stripped EXIF (WhatsApp
          forwards, screenshots) → capturedAt = uploadedAt + flag exifMissing=true;
          strip GPS; decode via Pillow + pillow-heif (iPhone HEIC); render thumb_384 /
          classify_768 / display_1600; set stages.thumb='done'
          DECODE FAILURE = PERMANENT (corrupt/masquerading file): status='rejected',
          delete object, no retries — never loop retries on a poisoned input
  videos: enqueue 'video-prep' task instead (ffprobe + poster + keyframes + proxy are too heavy for the intake path — see spec 03 §4)
  enqueue Cloud Tasks: classify-queue, face-queue, safety-queue — UNNAMED tasks
       (if doc.bountyId is set: classify goes to priority-queue instead — bounty
        validation must feel instant on a guest's phone; spec 05 §3, spec 09 §2)
  set status='processing'
```

- **Idempotency:** transaction guard on status/stage fields — NOT named tasks. (Named Cloud Tasks look tempting for dedupe but add dispatch latency and a task name cannot be reused for hours-to-days after completion, which breaks the replay endpoint. Handlers are idempotent anyway; duplicate task executions are absorbed by the same transaction guards.) Eventarc is at-least-once; duplicate finalize events are absorbed.
- **Failure:** Eventarc retries with backoff (10 s→600 s, 24 h retention); a **dead-letter topic** is configured on the underlying `eventarc-*` subscription. DLQ consumer marks the media doc `status='quarantined'` + writes an ops alert doc (host console shows a red badge — "failure handling" judging point).
- Orphan sweep: scheduled function marks `awaiting_upload` docs older than 24 h as `abandoned`.

## 6. Multi-user behavior at burst

200 guests × 30 photos in 10 minutes ≈ 10 uploads/s sustained, bursts of 50/s:
- Signed-URL issuance is a cheap Firestore batch write (≤ 50 docs/call) — Cloud Run scales horizontally, no shared state.
- GCS ingests bursts natively. Eventarc fans out; **Cloud Tasks queues are the throttle** (spec 03 §2) so Gemini spend-tier limits are respected while GCS/Firestore run ahead.
- Firestore media docs use ULID doc IDs (non-sequential); `createdAt` gets an **index exemption** (500 writes/s cap on sequential indexed fields).
- Per-batch progress: client-side only (outbox), no server aggregation needed.

## 7. Acceptance criteria

- [ ] Select 30 photos, kill the tab at ~photo 10, reopen → remaining 20 upload without user action; no duplicates in Firestore or GCS.
- [ ] Airplane mode mid-batch → items go `failed`/retry; restore network → drain completes.
- [ ] Same batch re-selected → same clientMediaIds rejected as duplicates (409) or resolved to existing docs.
- [ ] 60 s video uploads via resumable path; interrupted at 50% → resumes from committed offset, poster + proxy appear.
- [ ] Duplicate Eventarc delivery (replay test) causes no duplicate tasks/state.
- [ ] Signed URL expired → client transparently refreshes and completes.
- [ ] Two guests upload the byte-identical photo → second is marked `duplicateOf`, consumes no Gemini calls, never appears twice on any surface.
- [ ] A PUT whose body exceeds the declared Content-Length is rejected by GCS; an oversized object slipping through is deleted at intake.
- [ ] iPhone HEIC uploads decode and thumbnail correctly; a corrupt file is `rejected` permanently with zero retry loops.
