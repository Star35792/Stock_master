import type {
  OperationsAlertSummary,
  OperationsFailureDetail,
  OperationsFailureReport,
  OperationsOverview,
  OperationsRerunPlan,
} from "./types";

export async function fetchOperationsOverview(
  limit: number,
  signal?: AbortSignal,
): Promise<OperationsOverview> {
  const response = await fetch(`/api/v1/operations/overview?limit=${limit}`, {
    headers: {
      Accept: "application/json",
    },
    signal,
  });

  if (!response.ok) {
    throw new Error(`Overview request failed with ${response.status}`);
  }

  return response.json() as Promise<OperationsOverview>;
}

export async function fetchOperationsAlerts(
  signal?: AbortSignal,
): Promise<OperationsAlertSummary> {
  const response = await fetch(`/api/v1/operations/alerts`, {
    headers: {
      Accept: "application/json",
    },
    signal,
  });

  if (!response.ok) {
    throw new Error(`Alerts request failed with ${response.status}`);
  }

  return response.json() as Promise<OperationsAlertSummary>;
}

export async function fetchOperationsFailures(
  limit: number,
  signal?: AbortSignal,
): Promise<OperationsFailureReport> {
  const response = await fetch(`/api/v1/operations/failures?limit=${limit}`, {
    headers: {
      Accept: "application/json",
    },
    signal,
  });

  if (!response.ok) {
    throw new Error(`Failures request failed with ${response.status}`);
  }

  return response.json() as Promise<OperationsFailureReport>;
}

export async function fetchOperationsFailureDetail(
  failureType: OperationsFailureDetail["failure_type"],
  failureId: number,
  signal?: AbortSignal,
): Promise<OperationsFailureDetail> {
  const response = await fetch(
    `/api/v1/operations/failures/${failureType}/${failureId}`,
    {
      headers: {
        Accept: "application/json",
      },
      signal,
    },
  );

  if (!response.ok) {
    throw new Error(`Failure detail request failed with ${response.status}`);
  }

  return response.json() as Promise<OperationsFailureDetail>;
}

export async function fetchOperationsFailureRerunPlan(
  failureType: OperationsRerunPlan["failure_type"],
  failureId: number,
  signal?: AbortSignal,
): Promise<OperationsRerunPlan> {
  const response = await fetch(
    `/api/v1/operations/failures/${failureType}/${failureId}/rerun-plan`,
    {
      headers: {
        Accept: "application/json",
      },
      signal,
    },
  );

  if (!response.ok) {
    throw new Error(`Rerun plan request failed with ${response.status}`);
  }

  return response.json() as Promise<OperationsRerunPlan>;
}
