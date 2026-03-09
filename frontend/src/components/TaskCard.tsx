import type { TaskStatus } from "../api/client";
import { Link } from "react-router-dom";

const statusColor: Record<string, string> = {
  running: "#00b894",
  error: "#d63031",
  offline: "#636e72",
};

interface Props {
  task: TaskStatus;
}

export default function TaskCard({ task }: Props) {
  const color = statusColor[task.status] ?? "#636e72";
  return (
    <Link
      to={`/tasks/${encodeURIComponent(task.stream_id)}`}
      style={{ textDecoration: "none", color: "inherit" }}
    >
      <div
        style={{
          background: "#fff",
          borderRadius: 8,
          padding: 16,
          boxShadow: "0 1px 4px rgba(0,0,0,0.08)",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          cursor: "pointer",
          transition: "box-shadow 0.2s",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <strong>{task.stream_name}</strong>
          <span
            style={{
              background: color,
              color: "#fff",
              padding: "2px 10px",
              borderRadius: 12,
              fontSize: "0.75rem",
              fontWeight: 600,
            }}
          >
            {task.status}
          </span>
        </div>
        <div style={{ fontSize: "0.85rem", color: "#636e72" }}>
          ID: {task.stream_id}
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {task.labels.map((l) => (
            <span
              key={l}
              style={{
                background: "#dfe6e9",
                padding: "2px 8px",
                borderRadius: 4,
                fontSize: "0.75rem",
              }}
            >
              {l}
            </span>
          ))}
        </div>
      </div>
    </Link>
  );
}
