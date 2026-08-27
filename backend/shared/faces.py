"""The face index: vector search, and the only code that links a face to a person.

Two callers, deliberately sharing one module. `worker-face` writes embeddings and clusters;
`api` reads them back to answer "which of these faces are you". Keeping both halves of that
conversation in one file is what makes the thresholds impossible to drift apart — a claim that
used a different similarity convention than the indexer would be a silent privacy bug.

Three conventions hold everywhere below and are load-bearing:

1. **Every embedding is unit-norm** (InsightFace's `normed_embedding`), so cosine similarity is
   just the dot product and Firestore's COSINE distance is exactly `1 - similarity`. Every
   threshold in `settings` is a *similarity*; every number Firestore hands back is a *distance*.
   The conversion happens here and nowhere else.
2. **Matching people is an exact scan, not a vector query.** Spec 09 §3's index inventory has one
   vector index (`faces.embedding`) and spec 03 §5.2's `findNearest` against `people` would need a
   second. It would also be the wrong tool: enrolled people number in the dozens, an exact scan
   over dozens of vectors costs under a millisecond, and — unlike an index — it cannot be stale.
   Vector-index visibility lag is precisely what would make a just-enrolled bride invisible to
   the next photo. Same argmax, no approximation, no lag.
3. **`personId` on a face doc is a claim, not a guess.** The indexer writes it only on a ≥ τ_match
   match against an *enrolled* selfie; everything else is a cluster. The functions at the bottom
   of this file are the only other writers, and each one leaves an audit trail behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure
from google.cloud.firestore_v1.vector import Vector

from . import fs, log
from .settings import CLUSTER_PROBE_LIMIT, settings

EMBEDDING_DIM = 512
EMBEDDING_FIELD = "embedding"

#: Firestore hands the distance back in this synthetic field. Leading underscore so it can never
#: collide with a stored field name.
_DISTANCE_FIELD = "_dist"


# ---------------------------------------------------------------- vector arithmetic


def to_vector(values: Sequence[float]) -> Vector:
    if len(values) != EMBEDDING_DIM:
        raise ValueError(f"embedding must be {EMBEDDING_DIM}-d, got {len(values)}")
    return Vector([float(v) for v in values])


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two unit-norm vectors — i.e. their dot product.

    No renormalisation on purpose. If a vector reaching this function is not unit-norm, the number
    that comes out is wrong and the thresholds silently shift, which is a bug worth crashing on
    rather than smoothing over; `worker-face` asserts the norm at the point of production instead.
    """
    return float(sum(x * y for x, y in zip(a, b)))


def _similarity(doc: dict[str, Any]) -> float:
    """Firestore COSINE distance → cosine similarity."""
    return 1.0 - float(doc.get(_DISTANCE_FIELD) or 0.0)


# ---------------------------------------------------------------- people (exact scan)


@dataclass(frozen=True)
class PersonHit:
    personId: str
    similarity: float
    person: dict[str, Any]

    @property
    def tier(self) -> int:
        return int(self.person.get("tier") or 3)

    @property
    def protected(self) -> bool:
        """VIP or host-enrolled: spec 02 §3 routes a match on one of these to host approval.

        Tier ≤ 2 is Principal / InnerCircle / NamedVIP (spec 11 §3); `hostEnrolled` covers anyone
        the host named without a tier promotion. These are the albums worth stealing.
        """
        return self.tier <= 2 or bool(self.person.get("hostEnrolled"))


def enrolled_people(event_id: str) -> list[dict[str, Any]]:
    """Every person in this event who consented to a selfie embedding, with that embedding attached.

    Two reads, not one: the person document carries the identity a client may see (display name,
    tier, consent flags) and `enrollments/{personId}` carries the face template no client may ever
    see (see `fs.enrollments_col` for why the split exists). They are joined here, once, so every
    caller downstream still works with a single person dict and the security boundary costs one
    batched `get_all` per invocation — enrolled people number in the dozens, and this is the same
    scan-not-index reasoning as note 2 in this module's docstring.
    """
    people = {
        snap.id: (snap.to_dict() or {})
        for snap in fs.people_col(event_id)
        .where(filter=FieldFilter("consent.selfieEnrolled", "==", True))
        .stream()
    }
    if not people:
        return []

    refs = [fs.enrollment_ref(event_id, person_id) for person_id in people]
    for snap in fs.db().get_all(refs):
        if not snap.exists:
            continue
        embedding = (snap.to_dict() or {}).get("embedding")
        if embedding is not None:
            people[snap.id]["selfieEmbedding"] = embedding
    return list(people.values())


def match_people(
    event_id: str,
    embedding: Sequence[float],
    *,
    min_similarity: float,
    people: Iterable[dict[str, Any]] | None = None,
) -> list[PersonHit]:
    """Enrolled people whose selfie is within `min_similarity`, best first.

    Returns the *full ranked list* rather than the argmax because the ambiguity margin (spec 02 §3)
    is a property of the top *two* — twins at a wedding are a routine occurrence, not an edge case.
    """
    candidates = enrolled_people(event_id) if people is None else people
    hits: list[PersonHit] = []
    for person in candidates:
        stored = person.get("selfieEmbedding")
        person_id = str(person.get("personId") or "")
        if not person_id or not isinstance(stored, (list, Vector)):
            continue
        values = list(stored.value) if isinstance(stored, Vector) else list(stored)
        if len(values) != EMBEDDING_DIM:
            continue
        similarity = cosine(embedding, values)
        if similarity >= min_similarity:
            hits.append(PersonHit(person_id, similarity, person))
    hits.sort(key=lambda h: h.similarity, reverse=True)
    return hits


def is_ambiguous(hits: Sequence[PersonHit]) -> bool:
    """True when the top two matches are too close to tell apart (spec 02 §3, margin 0.08)."""
    if len(hits) < 2:
        return False
    return (hits[0].similarity - hits[1].similarity) < settings().claim_ambiguity_margin


# ---------------------------------------------------------------- faces (vector index)


@dataclass(frozen=True)
class FaceHit:
    faceId: str
    mediaId: str
    similarity: float
    personId: str | None
    clusterId: str | None
    box: dict[str, float]


def _face_hit(doc: dict[str, Any]) -> FaceHit:
    return FaceHit(
        faceId=str(doc.get("faceId") or ""),
        mediaId=str(doc.get("mediaId") or ""),
        similarity=_similarity(doc),
        personId=doc.get("personId") or None,
        clusterId=doc.get("clusterId") or None,
        box=dict(doc.get("box") or {}),
    )


def nearest_faces(
    event_id: str,
    embedding: Sequence[float],
    *,
    limit: int,
    min_similarity: float,
    exclude_media: str | None = None,
) -> list[FaceHit]:
    """Nearest indexed faces in *this event*, best first (spec 09 §3's one vector index).

    Event scoping is structural, not a filter: `faces` is a subcollection of the event, so a
    collection-scoped query cannot reach another event's faces even if asked to. Spec 02 §3's
    "this event only" is therefore enforced by the data model rather than by a `where` clause
    somebody could forget.

    `min_similarity` is pushed into Firestore as a distance threshold so the server stops
    scanning instead of shipping neighbours we would discard.
    """
    query = fs.faces_col(event_id).find_nearest(
        vector_field=EMBEDDING_FIELD,
        query_vector=to_vector(embedding),
        limit=max(1, limit),
        distance_measure=DistanceMeasure.COSINE,
        distance_result_field=_DISTANCE_FIELD,
        distance_threshold=1.0 - min_similarity,
    )
    hits = [_face_hit(snap.to_dict() or {}) for snap in query.get()]
    if exclude_media:
        # Other faces in the same photo are, by construction, *other people* — adopting a cluster
        # from one would merge two guests standing next to each other into one album.
        hits = [h for h in hits if h.mediaId != exclude_media]
    return [h for h in hits if h.faceId]


def nearest_cluster(
    event_id: str, embedding: Sequence[float], *, exclude_media: str
) -> FaceHit | None:
    """The cluster to adopt for an unmatched face (incremental threshold clustering, τ_cluster).

    Nearest-neighbour adoption rather than centroid comparison: it needs no cluster documents to
    keep in sync, and it is the shape spec 03 §5.2 tolerates split brain on — two workers may
    each mint a cluster for the same person, and the hourly reconciliation sweep merges them.
    """
    for hit in nearest_faces(
        event_id,
        embedding,
        limit=CLUSTER_PROBE_LIMIT,
        min_similarity=settings().tau_cluster,
        exclude_media=exclude_media,
    ):
        if hit.clusterId:
            return hit
    return None


# ---------------------------------------------------------------- linking (the audited writes)


def _group_by_media(face_ids_by_media: dict[str, list[str]]) -> list[tuple[str, list[str]]]:
    return sorted(face_ids_by_media.items())


def group_hits(hits: Iterable[FaceHit]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for hit in hits:
        if hit.mediaId and hit.faceId:
            grouped.setdefault(hit.mediaId, []).append(hit.faceId)
    return grouped


@firestore.transactional
def _link_one_media(
    transaction: firestore.Transaction,
    event_id: str,
    media_id: str,
    face_ids: list[str],
    person_id: str,
    claim_id: str | None,
    link: bool,
) -> int:
    """Link (or unlink) this media's faces and its `albumOf` membership in one atomic write.

    `albumOf` is the membership array spec 04 §3 relies on because Firestore cannot OR-query, so
    it must never disagree with `faces[].personId` on the same document — hence one transaction
    covering the face docs and the denormalised copy together.
    """
    media_ref = fs.media_ref(event_id, media_id)
    face_refs = [fs.face_ref(event_id, fid) for fid in face_ids]
    media_snap = media_ref.get(transaction=transaction)
    if not media_snap.exists:
        return 0
    face_snaps = list(fs.db().get_all(face_refs, transaction=transaction))

    touched = 0
    for snap in face_snaps:
        if not snap.exists:
            continue
        current = (snap.to_dict() or {}).get("personId") or None
        if link:
            # Never take a face that already belongs to somebody: a claim adds members, it never
            # reassigns them. Re-running the same claim is therefore free rather than destructive.
            if current and current != person_id:
                continue
            transaction.update(
                snap.reference,
                {"personId": person_id, "claimId": claim_id, "claimedAt": fs.SERVER_TIMESTAMP},
            )
        else:
            if current != person_id:
                continue
            transaction.update(
                snap.reference,
                {
                    "personId": None,
                    "claimId": None,
                    "claimedAt": fs.DELETE_FIELD,
                    "unclaimedAt": fs.SERVER_TIMESTAMP,
                },
            )
        touched += 1

    media = media_snap.to_dict() or {}
    wanted = set(face_ids)
    refs = [dict(ref) for ref in (media.get("faces") or [])]
    for ref in refs:
        if ref.get("faceId") not in wanted:
            continue
        if link and not ref.get("personId"):
            ref["personId"] = person_id
        elif not link and ref.get("personId") == person_id:
            ref["personId"] = None
    album = sorted({str(ref["personId"]) for ref in refs if ref.get("personId")})
    transaction.update(media_ref, {"faces": refs, "albumOf": album})
    return touched


def link_faces(
    event_id: str, person_id: str, face_ids_by_media: dict[str, list[str]], claim_id: str
) -> int:
    """Attach `person_id` to these faces. Returns how many links actually landed."""
    total = 0
    for media_id, face_ids in _group_by_media(face_ids_by_media):
        total += _link_one_media(
            fs.db().transaction(), event_id, media_id, face_ids, person_id, claim_id, True
        )
    log.info(
        "faces_linked",
        event_id=event_id,
        person=person_id,
        claim=claim_id,
        faces=total,
        media=len(face_ids_by_media),
    )
    return total


def faces_of_person(event_id: str, person_id: str) -> dict[str, list[str]]:
    query = fs.faces_col(event_id).where(filter=FieldFilter("personId", "==", person_id))
    grouped: dict[str, list[str]] = {}
    for snap in query.stream():
        doc = snap.to_dict() or {}
        media_id = str(doc.get("mediaId") or "")
        if media_id:
            grouped.setdefault(media_id, []).append(snap.id)
    return grouped


def unlink_person(event_id: str, person_id: str) -> int:
    """Return every face claimed by this person to unclaimed (spec 02 §8 — reversible claims).

    The face docs and their embeddings survive; only the identity link is removed, so the faces
    fall back to their cluster and the person's album empties. This is what makes a wrong claim a
    correctable mistake instead of a permanent one.
    """
    grouped = faces_of_person(event_id, person_id)
    total = 0
    for media_id, face_ids in _group_by_media(grouped):
        total += _link_one_media(
            fs.db().transaction(), event_id, media_id, face_ids, person_id, None, False
        )
    log.info("faces_unlinked", event_id=event_id, person=person_id, faces=total)
    return total
