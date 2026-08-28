// Host console API calls — mirrors backend/api/host.py's endpoints one-to-one (spec 08, spec 11).
// Kept apart from lib/api.ts for the same reason as hostTypes.ts: a separate, code-split surface.

import { ApiError, authedJson } from "./api";
import type {
  ConsoleSummary,
  CreateEventResponse,
  EventStageDoc,
  EventTemplateId,
  EventTypeProfile,
  HostLinkResponse,
  ItineraryParseOut,
  LifecycleResponse,
  RedeemHostResponse,
  RequiredMoment,
  SensitivityProfile,
  WrapReport,
} from "./hostTypes";

export { ApiError };

export async function createEvent(body: {
  name: string;
  timezone: string;
  templateId: EventTemplateId;
}): Promise<CreateEventResponse> {
  return authedJson("/v1/events", { method: "POST", body: JSON.stringify(body) });
}

export async function redeemHostCode(code: string): Promise<RedeemHostResponse> {
  return authedJson("/v1/host-claim", { method: "POST", body: JSON.stringify({ code }) });
}

export async function createHostLink(eventId: string): Promise<HostLinkResponse> {
  return authedJson(`/v1/events/${eventId}/host-links`, { method: "POST", body: "{}" });
}

export async function updateProfile(
  eventId: string,
  body: {
    templateId: EventTemplateId;
    vipTopology?: "pyramid" | "flat";
    sensitivityProfile?: SensitivityProfile;
    culturalGlossary?: string[];
    requiredMomentsTemplate?: RequiredMoment[];
  }
): Promise<{ eventTypeProfile: EventTypeProfile }> {
  return authedJson(`/v1/events/${eventId}/profile`, { method: "POST", body: JSON.stringify(body) });
}

export async function parseItinerary(eventId: string, rawText: string): Promise<ItineraryParseOut> {
  return authedJson(`/v1/events/${eventId}/itinerary/parse`, {
    method: "POST",
    body: JSON.stringify({ rawText }),
  });
}

export async function saveStages(
  eventId: string,
  stages: EventStageDoc[]
): Promise<{ stages: EventStageDoc[] }> {
  return authedJson(`/v1/events/${eventId}/stages`, { method: "PUT", body: JSON.stringify({ stages }) });
}

export async function goLive(eventId: string): Promise<LifecycleResponse> {
  return authedJson(`/v1/events/${eventId}/lifecycle/go-live`, { method: "POST", body: "{}" });
}

export async function pauseEvent(eventId: string): Promise<LifecycleResponse> {
  return authedJson(`/v1/events/${eventId}/lifecycle/pause`, { method: "POST", body: "{}" });
}

export async function resumeEvent(eventId: string): Promise<LifecycleResponse> {
  return authedJson(`/v1/events/${eventId}/lifecycle/resume`, { method: "POST", body: "{}" });
}

export async function wrapEvent(eventId: string): Promise<LifecycleResponse> {
  return authedJson(`/v1/events/${eventId}/lifecycle/wrap`, { method: "POST", body: "{}" });
}

export async function finalizeEvent(eventId: string): Promise<WrapReport> {
  return authedJson(`/v1/events/${eventId}/lifecycle/finalize`, { method: "POST", body: "{}" });
}

export async function getWrapReport(eventId: string): Promise<WrapReport> {
  return authedJson(`/v1/events/${eventId}/wrap-report`, { method: "GET" });
}

export async function setStageOverride(
  eventId: string,
  stageId: string | null
): Promise<{ stageOverride: string | null }> {
  return authedJson(`/v1/events/${eventId}/stage-override`, {
    method: "POST",
    body: JSON.stringify({ stageId }),
  });
}

export async function setFreeze(
  eventId: string,
  frozen: boolean
): Promise<{ publicFrozen: boolean }> {
  return authedJson(`/v1/events/${eventId}/freeze`, {
    method: "POST",
    body: JSON.stringify({ frozen }),
  });
}

export async function getConsoleSummary(eventId: string): Promise<ConsoleSummary> {
  return authedJson(`/v1/events/${eventId}/console`, { method: "GET" });
}
