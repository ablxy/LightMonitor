import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { fetchTaskDetail, type TaskDetail as TD } from "../api/client";
import ResultLog from "../components/ResultLog";

export default function TaskDetail() {
  const { taskId } = useParams<{ taskId: string }>();
  const [detail, setDetail] = useState<TD | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!taskId) return;
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchTaskDetail(taskId);
        if (!cancelled) setDetail(data);
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    };
    load();
    const interval = setInterval(load, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [taskId]);

  if (error) return <p style={{ color: "#d63031" }}>Error: {error}</p>;
  if (!detail) return <p>Loading task…</p>;

  const { task, recent_results } = detail;
  const latestImage =
    recent_results.length > 0 ? recent_results[0].image_base64 : null;

  return (
    <div>
      <Link to="/" style={{ color: "#0984e3", fontSize: "0.9rem" }}>
        ← Back to tasks
      </Link>

      <h2 style={{ margin: "16px 0 8px" }}>{task.stream_name}</h2>
      <p style={{ color: "#636e72", marginBottom: 16 }}>
        Stream ID: {task.stream_id} &nbsp;|&nbsp; Status:{" "}
        <strong>{task.status}</strong>
      </p>

      <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
        {/* Latest frame */}
        <div style={{ flex: "1 1 400px" }}>
          <h3 style={{ marginBottom: 8 }}>Latest Frame</h3>
          {latestImage ? (
            <img
              src={`data:image/jpeg;base64,${latestImage}`}
              alt="Latest frame"
              style={{ width: "100%", borderRadius: 8, border: "1px solid #dfe6e9" }}
            />
          ) : (
            <div
              style={{
                background: "#dfe6e9",
                borderRadius: 8,
                height: 300,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "#636e72",
              }}
            >
              No frames captured yet
            </div>
          )}
        </div>

        {/* Detection results */}
        <div style={{ flex: "1 1 300px" }}>
          <h3 style={{ marginBottom: 8 }}>Detection Results</h3>
          <ResultLog results={recent_results} />
        </div>
      </div>
    </div>
  );
}
