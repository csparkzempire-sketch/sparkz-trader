import { useEffect, useState } from "react";
import { api } from "../services/api";
import { useAppState } from "../hooks/useAppState";
import type { ModelSummary, TrainModelResponse } from "../types/api";
import { DisclaimerNote, ErrorBanner, LoadingBlock, PageHeader, StatCard, SymbolTimeframePicker } from "../components/ui";

const MODEL_TYPES = [
  { value: "logistic_regression", label: "Logistic Regression" },
  { value: "random_forest", label: "Random Forest" },
  { value: "xgboost", label: "XGBoost (if installed)" },
];

export default function ModelLabPage() {
  const { symbol, timeframe, setLastModelId } = useAppState();
  const [modelType, setModelType] = useState("random_forest");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [trained, setTrained] = useState<TrainModelResponse | null>(null);
  const [models, setModels] = useState<ModelSummary[]>([]);

  const refreshModels = () => api.listModels().then(setModels).catch(() => {});

  useEffect(() => {
    refreshModels();
  }, []);

  const train = () => {
    setLoading(true);
    setError(null);
    api
      .trainModel({ symbol, timeframe, model_type: modelType, hyperparameters: {} })
      .then((r) => {
        setTrained(r);
        setLastModelId(r.model_id);
        refreshModels();
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  return (
    <div>
      <PageHeader title="Model Lab" subtitle="Train, evaluate, and compare classification models" />
      <div className="px-6 pb-6 flex items-center gap-4">
        <SymbolTimeframePicker />
        <select
          value={modelType}
          onChange={(e) => setModelType(e.target.value)}
          className="bg-base-bg border border-base-border rounded px-2 py-1.5 text-sm text-base-text"
        >
          {MODEL_TYPES.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
        <button
          onClick={train}
          disabled={loading}
          className="bg-accent-brand text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50"
        >
          {loading ? "Training…" : "Train Model"}
        </button>
      </div>

      <div className="px-6 pb-8 space-y-4">
        {error && <ErrorBanner message={error} />}
        {loading && <LoadingBlock label="Building leak-safe dataset, splitting chronologically, and fitting the model…" />}

        {!loading && trained && (
          <>
            <div className="panel p-4">
              <div className="grid grid-cols-3 gap-4 text-sm">
                <div>
                  <div className="stat-label">Train Period</div>
                  <div className="text-xs font-mono-nums mt-1">
                    {trained.train_period[0]} → {trained.train_period[1]}
                  </div>
                </div>
                <div>
                  <div className="stat-label">Validation Period</div>
                  <div className="text-xs font-mono-nums mt-1">
                    {trained.validation_period[0]} → {trained.validation_period[1]}
                  </div>
                </div>
                <div>
                  <div className="stat-label">Test Period</div>
                  <div className="text-xs font-mono-nums mt-1">
                    {trained.test_period[0]} → {trained.test_period[1]}
                  </div>
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="panel p-4">
                <div className="panel-header -mx-4 -mt-4 mb-3">Validation Metrics</div>
                <MetricGrid m={trained.classification_metrics_validation} />
              </div>
              <div className="panel p-4">
                <div className="panel-header -mx-4 -mt-4 mb-3">Test Metrics (Untouched Until Now)</div>
                <MetricGrid m={trained.classification_metrics_test} />
              </div>
            </div>

            {trained.feature_importance.length > 0 && (
              <div className="panel p-4">
                <div className="panel-header -mx-4 -mt-4 mb-3">Feature Importance (Top 10)</div>
                <div className="space-y-1.5">
                  {trained.feature_importance.slice(0, 10).map((f) => (
                    <div key={f.feature} className="flex items-center gap-3 text-xs">
                      <div className="w-32 text-base-muted font-mono-nums truncate">{f.feature}</div>
                      <div className="flex-1 h-2 bg-base-bg rounded overflow-hidden border border-base-border">
                        <div
                          className="h-full bg-accent-brand"
                          style={{ width: `${Math.min(100, f.importance * 100 * 5)}%` }}
                        />
                      </div>
                      <div className="w-16 text-right font-mono-nums text-base-muted">{f.importance.toFixed(4)}</div>
                    </div>
                  ))}
                </div>
                <p className="text-[11px] text-base-muted mt-3">
                  Feature importance reflects the model's internal weighting, not causal influence on price.
                </p>
              </div>
            )}

            <DisclaimerNote>{trained.disclaimer}</DisclaimerNote>
          </>
        )}

        <div className="panel">
          <div className="panel-header">Saved Models</div>
          <div className="p-4">
            {models.length === 0 ? (
              <div className="text-sm text-base-muted">No models trained yet.</div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-base-muted text-xs uppercase tracking-wider">
                    <th className="pb-2">Model ID</th>
                    <th className="pb-2">Type</th>
                    <th className="pb-2">Symbol</th>
                    <th className="pb-2">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m) => (
                    <tr key={m.model_id} className="border-t border-base-border/50 font-mono-nums text-xs">
                      <td className="py-2">{m.model_id}</td>
                      <td className="py-2">{m.model_type}</td>
                      <td className="py-2">{m.symbol}</td>
                      <td className="py-2 text-base-muted">{new Date(m.created_at).toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function MetricGrid({ m }: { m: TrainModelResponse["classification_metrics_test"] }) {
  return (
    <div className="grid grid-cols-3 gap-3">
      <StatCard label="Accuracy" value={m.accuracy.toFixed(3)} />
      <StatCard label="Precision" value={m.precision.toFixed(3)} />
      <StatCard label="Recall" value={m.recall.toFixed(3)} />
      <StatCard label="F1" value={m.f1.toFixed(3)} />
      <StatCard label="ROC-AUC" value={m.roc_auc != null ? m.roc_auc.toFixed(3) : "—"} />
      <StatCard label="N Samples" value={String(m.n_samples)} />
    </div>
  );
}
