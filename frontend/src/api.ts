import type {
  OperationsAlertSummary,
  OperationsFailureDetail,
  OperationsFailureReport,
  OperationsOverview,
  OperationsRerunPlan,
  ApiPage,
  FactorDefinition,
  FactorProfile,
  PaperAccount,
  BacktestFinalHoldings,
  PaperPerformance,
  PaperPositions,
  PortfolioPlan,
  PortfolioPlanCreateRequest,
  PortfolioPlanDrift,
  PortfolioRiskReadiness,
  PositionBook,
  PositionBookHoldings,
  PositionTrade,
  PaperEquitySnapshot,
  PaperExecutionApproval,
  PaperExecution,
  RiskEvaluation,
} from "./types";

export async function fetchPaperAccounts(): Promise<PaperAccount[]> {
  return jsonApi(
    await fetch("/api/v1/paper-accounts", { headers: { Accept: "application/json" } }),
    "模拟账户读取失败",
  );
}

async function jsonApi<T>(response: Response, message: string): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `${message} (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function fetchFactorDefinitions(filters: {
  name?: string;
  category?: string;
  frequency?: string;
  is_active?: boolean;
} = {}): Promise<ApiPage<FactorDefinition>> {
  const params = new URLSearchParams({ limit: "200" });
  if (filters.name) params.set("name", filters.name);
  if (filters.category) params.set("category", filters.category);
  if (filters.frequency) params.set("frequency", filters.frequency);
  if (filters.is_active !== undefined) params.set("is_active", String(filters.is_active));
  return jsonApi(
    await fetch(`/api/v1/factors/definitions?${params.toString()}`, { headers: { Accept: "application/json" } }),
    "因子定义读取失败",
  );
}

export async function fetchFactorProfiles(filters: {
  factor_name: string;
  factor_version: string;
}): Promise<ApiPage<FactorProfile>> {
  const params = new URLSearchParams({
    factor_name: filters.factor_name,
    factor_version: filters.factor_version,
    limit: "100",
  });
  return jsonApi(
    await fetch(`/api/v1/factors/profiles?${params.toString()}`, { headers: { Accept: "application/json" } }),
    "因子画像读取失败",
  );
}

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

async function portfolioResponse(response: Response): Promise<PortfolioPlan> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Portfolio request failed with ${response.status}`);
  }
  return response.json() as Promise<PortfolioPlan>;
}

export async function createPortfolioPlan(
  payload: PortfolioPlanCreateRequest,
  idempotencyKey: string,
): Promise<PortfolioPlan> {
  const response = await fetch("/api/v1/portfolio/plans", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(payload),
  });
  return portfolioResponse(response);
}

export async function fetchPortfolioPlan(planId: number): Promise<PortfolioPlan> {
  const response = await fetch(`/api/v1/portfolio/plans/${planId}`, {
    headers: { Accept: "application/json" },
  });
  return portfolioResponse(response);
}

export async function fetchPortfolioRiskReadiness(
  planId: number,
  paperAccountId: number,
): Promise<PortfolioRiskReadiness> {
  const response = await fetch(
    `/api/v1/portfolio/plans/${planId}/risk-readiness?paper_account_id=${paperAccountId}`,
    { headers: { Accept: "application/json" } },
  );
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Readiness request failed with ${response.status}`);
  }
  return response.json() as Promise<PortfolioRiskReadiness>;
}

export async function capturePaperEquitySnapshot(
  accountId: number,
  valuationDate: string,
  idempotencyKey: string,
): Promise<PaperEquitySnapshot> {
  const response = await fetch(`/api/v1/paper-accounts/${accountId}/equity-snapshots`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({ valuation_date: valuationDate }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Equity snapshot request failed with ${response.status}`);
  }
  return response.json() as Promise<PaperEquitySnapshot>;
}

export async function createRiskEvaluation(
  planId: number,
  accountId: number,
  equitySnapshotId: number,
  idempotencyKey: string,
): Promise<RiskEvaluation> {
  const response = await fetch(`/api/v1/portfolio/plans/${planId}/risk-evaluations`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({
      paper_account_id: accountId,
      equity_snapshot_id: equitySnapshotId,
    }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Risk evaluation request failed with ${response.status}`);
  }
  return response.json() as Promise<RiskEvaluation>;
}

export async function createPaperExecutionApproval(
  riskCheckId: number,
  evaluationHash: string,
  confirmationPhrase: string,
  idempotencyKey: string,
): Promise<PaperExecutionApproval> {
  const response = await fetch(`/api/v1/risk-evaluations/${riskCheckId}/confirmations`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({
      expected_evaluation_hash: evaluationHash,
      confirmation_phrase: confirmationPhrase,
    }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Approval request failed with ${response.status}`);
  }
  return response.json() as Promise<PaperExecutionApproval>;
}

export async function fetchPaperExecutionApproval(
  approvalId: number,
): Promise<PaperExecutionApproval> {
  const response = await fetch(`/api/v1/paper-execution-approvals/${approvalId}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Approval request failed with ${response.status}`);
  }
  return response.json() as Promise<PaperExecutionApproval>;
}

export async function revokePaperExecutionApproval(
  approvalId: number,
  reason: string,
  idempotencyKey: string,
): Promise<PaperExecutionApproval> {
  const response = await fetch(`/api/v1/paper-execution-approvals/${approvalId}/revoke`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({ reason }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Approval revoke failed with ${response.status}`);
  }
  return response.json() as Promise<PaperExecutionApproval>;
}

export async function executePaperApproval(
  approvalId: number,
  approvalHash: string,
  idempotencyKey: string,
): Promise<PaperExecution> {
  const response = await fetch(`/api/v1/paper-execution-approvals/${approvalId}/executions`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({ expected_approval_hash: approvalHash }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Paper execution failed with ${response.status}`);
  }
  return response.json() as Promise<PaperExecution>;
}

export async function fetchPaperExecution(executionId: number): Promise<PaperExecution> {
  const response = await fetch(`/api/v1/paper-executions/${executionId}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Paper execution request failed with ${response.status}`);
  }
  return response.json() as Promise<PaperExecution>;
}

export async function fetchPaperExecutionByApproval(
  approvalId: number,
): Promise<PaperExecution> {
  const response = await fetch(`/api/v1/paper-execution-approvals/${approvalId}/execution`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Paper execution request failed with ${response.status}`);
  }
  return response.json() as Promise<PaperExecution>;
}

export async function fetchPositionBooks(): Promise<PositionBook[]> {
  const response = await fetch("/api/v1/position-books", {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Position book list failed with ${response.status}`);
  }
  return response.json() as Promise<PositionBook[]>;
}

export async function createPositionBook(payload: {
  name: string;
  cash?: string;
  note?: string | null;
}): Promise<PositionBook> {
  const response = await fetch("/api/v1/position-books", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Position book creation failed with ${response.status}`);
  }
  return response.json() as Promise<PositionBook>;
}

export async function updatePositionBook(
  bookId: number,
  payload: { name?: string; cash?: string; note?: string | null },
): Promise<PositionBook> {
  const response = await fetch(`/api/v1/position-books/${bookId}`, {
    method: "PATCH",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Position book update failed with ${response.status}`);
  }
  return response.json() as Promise<PositionBook>;
}

export async function fetchPositionBookHoldings(bookId: number): Promise<PositionBookHoldings> {
  const response = await fetch(`/api/v1/position-books/${bookId}/holdings`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Position holdings failed with ${response.status}`);
  }
  return response.json() as Promise<PositionBookHoldings>;
}

export async function fetchPositionTrades(bookId: number): Promise<PositionTrade[]> {
  const response = await fetch(`/api/v1/position-books/${bookId}/trades`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Position trades failed with ${response.status}`);
  }
  return response.json() as Promise<PositionTrade[]>;
}

export async function createPositionTrade(
  bookId: number,
  payload: {
    side: "buy" | "sell";
    symbol: string;
    quantity: number;
    price: string;
    fee?: string;
    trade_date: string;
    note?: string | null;
  },
  idempotencyKey: string,
): Promise<PositionTrade> {
  const response = await fetch(`/api/v1/position-books/${bookId}/trades`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Position trade failed with ${response.status}`);
  }
  return response.json() as Promise<PositionTrade>;
}

export async function deletePositionTrade(bookId: number, tradeId: number): Promise<void> {
  const response = await fetch(`/api/v1/position-books/${bookId}/trades/${tradeId}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Position trade deletion failed with ${response.status}`);
  }
}

export async function fetchPaperAccountPositions(accountId: number): Promise<PaperPositions> {
  const response = await fetch(`/api/v1/paper-accounts/${accountId}/positions`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Paper positions failed with ${response.status}`);
  }
  return response.json() as Promise<PaperPositions>;
}

export async function fetchPaperAccountPerformance(accountId: number): Promise<PaperPerformance> {
  const response = await fetch(`/api/v1/paper-accounts/${accountId}/performance`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Paper performance failed with ${response.status}`);
  }
  return response.json() as Promise<PaperPerformance>;
}

export async function fetchPortfolioPlanDrift(
  planId: number,
  source: { positionBookId?: number; paperAccountId?: number; threshold?: string },
): Promise<PortfolioPlanDrift> {
  const params = new URLSearchParams();
  if (source.positionBookId !== undefined) {
    params.set("position_book_id", String(source.positionBookId));
  }
  if (source.paperAccountId !== undefined) {
    params.set("paper_account_id", String(source.paperAccountId));
  }
  if (source.threshold !== undefined) {
    params.set("threshold", source.threshold);
  }
  const response = await fetch(`/api/v1/portfolio/plans/${planId}/drift?${params.toString()}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: unknown;
      code?: unknown;
      symbols?: unknown;
    } | null;
    const message = typeof payload?.detail === "string"
      ? payload.detail
      : `Plan drift failed with ${response.status}`;
    const code = typeof payload?.code === "string" ? payload.code : null;
    const symbols = Array.isArray(payload?.symbols)
      ? payload.symbols.filter((value): value is string => typeof value === "string")
      : [];
    throw new PortfolioDriftApiError(message, response.status, code, symbols);
  }
  return response.json() as Promise<PortfolioPlanDrift>;
}

export class PortfolioDriftApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly symbols: string[];

  constructor(message: string, status: number, code: string | null, symbols: string[]) {
    super(message);
    this.name = "PortfolioDriftApiError";
    this.status = status;
    this.code = code;
    this.symbols = symbols;
  }
}

export async function fetchBacktestFinalHoldings(
  runId: number,
): Promise<BacktestFinalHoldings> {
  const response = await fetch(`/api/v1/backtests/runs/${runId}/final-holdings`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Backtest holdings failed with ${response.status}`);
  }
  return response.json() as Promise<BacktestFinalHoldings>;
}
