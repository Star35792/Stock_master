import type { OperationsOverview } from "./types";

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
