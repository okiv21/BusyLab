import type {
  Alert,
  Answer,
  Columns,
  DigestPreview,
  Goal,
  GoalProgress,
  Job,
  NewGoal,
  Story,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8000";

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/**
 * Whether this build is pointing at a developer's own machine while running
 * somewhere else.
 *
 * NEXT_PUBLIC_API is inlined at build time, so a deploy that forgets it does
 * not fail - it silently ships a site that asks every visitor whether their
 * API is running on port 8000. That reads as a broken product rather than a
 * missing setting, so it is worth naming exactly.
 */
function misconfigured(): boolean {
  if (typeof window === "undefined") return false;
  const local = /^(localhost|127\.0\.0\.1|\[::1\])$/;
  try {
    return local.test(new URL(BASE).hostname) && !local.test(window.location.hostname);
  } catch {
    return false;
  }
}

function unreachable(): ApiError {
  if (misconfigured()) {
    return new ApiError(
      "This site was built without NEXT_PUBLIC_API set, so it is looking " +
        "for the API on this machine rather than on the server. Set " +
        "NEXT_PUBLIC_API to the API's public URL and redeploy.",
      0
    );
  }
  // Naming the address is the whole diagnosis. A browser deliberately hides
  // whether a failed request was refused, timed out, or blocked by CORS, so
  // the one useful fact left is which address was tried - which immediately
  // separates a wrong variable from a sleeping server from a CORS mismatch.
  return new ApiError(`Cannot reach the BusyLab API at ${BASE}.`, 0);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, init);
  } catch {
    throw unreachable();
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      /* the body was not JSON; the status text will do */
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export { ApiError };

export function uploadFile(file: File) {
  const body = new FormData();
  body.append("file", file);
  return request<{ job_id: string; dataset_id: string; status: string }>(
    "/uploads",
    { method: "POST", body }
  );
}

export function getJob(id: string) {
  return request<Job>(`/jobs/${id}`);
}

export function getColumns(datasetId: string) {
  return request<Columns>(`/datasets/${datasetId}/columns`);
}

export function confirmColumns(datasetId: string, roles: Record<string, string>) {
  return request<{ job_id: string; dataset_id: string }>(
    `/datasets/${datasetId}/columns`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ roles }),
    }
  );
}

export function getStory(datasetId: string) {
  return request<Story>(`/datasets/${datasetId}/story`);
}

export function setDigestRecipient(datasetId: string, email: string) {
  return request<void>(`/datasets/${datasetId}/recipient`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
}

export function sendDigest(datasetId: string) {
  return request<{ sent: boolean; detail: string; recipient: string }>(
    `/datasets/${datasetId}/digest/send`,
    { method: "POST" }
  );
}

export function listGoals(datasetId: string) {
  return request<{ goals: Goal[]; progress: GoalProgress[] }>(
    `/datasets/${datasetId}/goals`
  );
}

export function createGoal(datasetId: string, goal: NewGoal) {
  return request<{ goal: Goal; job_id: string }>(`/datasets/${datasetId}/goals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(goal),
  });
}

export function deleteGoal(datasetId: string, goalId: string) {
  return request<{ deleted: string; job_id: string }>(
    `/datasets/${datasetId}/goals/${goalId}`,
    { method: "DELETE" }
  );
}

export function listAlerts(datasetId: string) {
  return request<{ alerts: Alert[] }>(`/datasets/${datasetId}/alerts`);
}

export function acknowledgeAlert(datasetId: string, key: string) {
  return request<void>(`/datasets/${datasetId}/alerts/${key}/acknowledge`, {
    method: "POST",
  });
}

export function getDigest(datasetId: string) {
  return request<DigestPreview>(`/datasets/${datasetId}/digest`);
}

export function ask(datasetId: string, question: string) {
  return request<Answer>(`/datasets/${datasetId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
}

/**
 * Poll a job to completion.
 *
 * Analysis runs off the request cycle, so the UI polls rather than blocks.
 * `onStep` receives the worker's own progress text, which is why the
 * analysing screen shows real stages instead of an invented percentage.
 */
export async function waitForJob(
  jobId: string,
  onStep?: (job: Job) => void,
  { intervalMs = 700, timeoutMs = 180_000 } = {}
): Promise<Job> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const job = await getJob(jobId);
    onStep?.(job);
    if (job.status === "done" || job.status === "failed") return job;
    if (Date.now() > deadline) {
      throw new ApiError("This is taking longer than expected.", 504);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
