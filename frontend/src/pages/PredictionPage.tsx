import { useState } from "react";
import { api } from "../services/api";
import { useAppState } from "../hooks/useAppState";
import type { ModelSummary, PredictResponse } from "../types/api";
import { DisclaimerNote, ErrorBanner, PageHeader, SignalBadge, SymbolTimeframePicker } from "../components/ui";
import { useEffect } from "react";

export default function PredictionPage() {
  const { symbol, timeframe, lastModelId, setLastModelId } = useAppState();
  const [models, setModels] = useState<ModelSummary[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>(lastModelId ?? "");
  const [result, setResult] = useState<PredictResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.listModels().then(setModels).catch(() => setModels([]));
  }, []);

  useEffect(() => {
    if (lastModelId) setSelectedModel(lastModelId);
  }, [lastModelId]);

  const runPredict = () => {
    if (!selectedModel) {
      setError("Select a trained model first (train one in Model Lab if the list is empty).");
      return;
    }
    setLoading(true);
    setError(null);
    api
      .predict(selectedModel, symbol, timeframe)
      .then((r) => {
        setResult(r);
        setLastModelId(selectedModel);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  return (
    <div>
      <PageHeader title="AI Prediction" subtitle="Model-estimated probability of the configured target event — never a certainty" />
      <div className="px-6 pb-6 flex items-center gap-4">
        <SymbolTimeframePicker />
        <select
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
          className="bg-base-bg border border-base-border rounded px-2 py-1.5 text-sm text-base-text min-w-[240px]"
        >
          <option value="">Select a trained model…</option>
          {models.map((m) => (
            <option key={m.model_id} value={m.model_id}>
              {m.model_type} · {m.symbol} · {m.model_id.slice(-8)}
            </option>
          ))}
        </select>
        <button
          onClick={runPredict}
          disabled={loading}
          className="bg-accent-brand text-white text-sm px-4 py-1.5 rounded hover:bg-blue-600 disabled:opacity-50"
        >
          {loading ? "Running…" : "Get Prediction"}
        </button>
      </div>

      <div className="px-6 pb-8 space-y-4">
        {error && <ErrorBanner message={error} />}
        {models.length === 0 && !error && (
          <div className="panel p-4 text-sm text-base-muted">
            No trained models yet. Head to Model Lab to train one first.
          </div>
        )}

        {result && (
          <div className="panel p-6">
            <div className="flex items-center justify-between mb-6">
              <div>
                <div className="text-xs uppercase tracking-wider text-base-muted">Model Estimate</div>
                <div className="text-sm text-base-muted mt-1">
                  {result.symbol} · as of {new Date(result.timestamp).toLocaleString()}
                </div>
              </div>
              <SignalBadge signal={result.signal} />
            </div>

            <div className="grid grid-cols-2 gap-6 mb-6">
              <div>
                <div className="stat-label mb-2">Probability Up</div>
                <div className="h-3 bg-base-bg rounded overflow-hidden border border-base-border">
                  <div
                    className="h-full bg-accent-up"
                    style={{ width: `${(result.probability_up * 100).toFixed(1)}%` }}
                  />
                </div>
                <div className="stat-value mt-1 text-accent-up">{(result.probability_up * 100).toFixed(1)}%</div>
              </div>
              <div>
                <div className="stat-label mb-2">Probability Down</div>
                <div className="h-3 bg-base-bg rounded overflow-hidden border border-base-border">
                  <div
                    className="h-full bg-accent-down"
                    style={{ width: `${(result.probability_down * 100).toFixed(1)}%` }}
                  />
                </div>
                <div className="stat-value mt-1 text-accent-down">{(result.probability_down * 100).toFixed(1)}%</div>
              </div>
            </div>

            <div className="border-t border-base-border pt-4">
              <div className="stat-label mb-1">Model Explanation</div>
              <p className="text-sm text-base-text leading-relaxed">{result.explanation}</p>
            </div>

            <div className="border-t border-base-border pt-4 mt-4">
              <div className="stat-label mb-1">Model ID</div>
              <div className="text-xs font-mono-nums text-base-muted">{result.model_id}</div>
            </div>
          </div>
        )}

        <DisclaimerNote>
          This is a probabilistic research output derived from historical patterns under the current model and data
          assumptions — it is not a guarantee, and past accuracy does not guarantee future accuracy. Model
          probabilities should be interpreted alongside a backtest of the resulting signals, not on their own.
        </DisclaimerNote>
      </div>
    </div>
  );
}
