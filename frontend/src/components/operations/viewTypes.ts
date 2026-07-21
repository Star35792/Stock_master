import type {
  BacktestRunFailure,
  DataQualityFailure,
  FactorRunFailure,
  JobFailure,
  OperationsFailureDetail,
  PaperOrderFailure,
} from "../../types";

export type FailureType = OperationsFailureDetail["failure_type"];
export type FailureItem =
  | DataQualityFailure
  | JobFailure
  | FactorRunFailure
  | BacktestRunFailure
  | PaperOrderFailure;

export interface SelectedFailure {
  type: FailureType;
  id: number;
  label: string;
}

export interface FailureSectionView {
  type: FailureType;
  title: string;
  total: number;
  hasMore: boolean;
  items: FailureItem[];
}
