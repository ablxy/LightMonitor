import type { FrameResult } from "../api/client";

interface Props {
  results: FrameResult[];
}

export default function ResultLog({ results }: Props) {
  if (results.length === 0) {
    return <p style={{ color: "#636e72" }}>No detection results yet.</p>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {results.map((r, idx) => (
        <div
          key={`${r.timestamp_ms}-${idx}`}
          style={{
            background: r.alarmed ? "#ffeaa7" : "#fff",
            border: r.alarmed ? "1px solid #fdcb6e" : "1px solid #dfe6e9",
            borderRadius: 8,
            padding: 12,
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
            <span style={{ fontWeight: 600 }}>
              {new Date(r.timestamp_ms).toLocaleTimeString()}
            </span>
            {r.alarmed && (
              <span style={{ color: "#d63031", fontWeight: 700, fontSize: "0.85rem" }}>
                ⚠ ALARM
              </span>
            )}
          </div>
          {r.detections.length > 0 ? (
            <ul style={{ paddingLeft: 18, margin: 0 }}>
              {r.detections.map((d, di) => (
                <li key={di} style={{ fontSize: "0.85rem" }}>
                  <strong>{d.label}</strong> – {(d.confidence * 100).toFixed(1)}%
                </li>
              ))}
            </ul>
          ) : (
            <span style={{ fontSize: "0.85rem", color: "#636e72" }}>No detections</span>
          )}
        </div>
      ))}
    </div>
  );
}
