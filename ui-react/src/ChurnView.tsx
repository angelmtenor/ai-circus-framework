import { useState } from "react";
import { predict, type PredictionResult } from "./apiClient";
import { config } from "./config";
import { ChatPanel } from "./ChatPanel";

export function ChurnView({ accessToken }: { accessToken: string | null }) {
  const [record, setRecord] = useState({
    CreditScore: 650,
    Geography: "France",
    Age: 40,
    Tenure: 3,
    Balance: 50000,
    NumOfProducts: 2,
    HasCrCard: 1,
    IsActiveMember: 1,
    EstimatedSalary: 75000,
  });
  const [result, setResult] = useState<PredictionResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  function update<K extends keyof typeof record>(key: K, value: (typeof record)[K]) {
    setRecord((r) => ({ ...r, [key]: value }));
  }

  async function runPredict() {
    setError(null);
    try {
      const response = await predict(config.predictionUrl, [record], accessToken);
      setResult(response.predictions[0]);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <div>
      <h2>📉 Customer Churn Prediction</h2>
      <div className="churn-form">
        <label>
          Credit score
          <input
            type="number"
            value={record.CreditScore}
            onChange={(e) => update("CreditScore", Number(e.target.value))}
          />
        </label>
        <label>
          Geography
          <select value={record.Geography} onChange={(e) => update("Geography", e.target.value)}>
            <option>France</option>
            <option>Germany</option>
            <option>Spain</option>
          </select>
        </label>
        <label>
          Age
          <input type="number" value={record.Age} onChange={(e) => update("Age", Number(e.target.value))} />
        </label>
        <label>
          Tenure (years)
          <input type="number" value={record.Tenure} onChange={(e) => update("Tenure", Number(e.target.value))} />
        </label>
        <label>
          Balance
          <input type="number" value={record.Balance} onChange={(e) => update("Balance", Number(e.target.value))} />
        </label>
        <label>
          Number of products
          <input
            type="number"
            value={record.NumOfProducts}
            onChange={(e) => update("NumOfProducts", Number(e.target.value))}
          />
        </label>
        <label>
          Estimated salary
          <input
            type="number"
            value={record.EstimatedSalary}
            onChange={(e) => update("EstimatedSalary", Number(e.target.value))}
          />
        </label>
      </div>
      <button onClick={runPredict}>Predict churn risk</button>
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="prediction-result">
          <p>
            <strong>Churn probability:</strong> {(result.probability * 100).toFixed(1)}%
          </p>
          <ul>
            {Object.entries(result.contributions).map(([feature, value]) => (
              <li key={feature}>
                {feature}: {value.toFixed(4)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <hr />
      <h3>💬 Ask about this data</h3>
      <ChatPanel baseUrl={config.assistantUrl} accessToken={accessToken} />
    </div>
  );
}
