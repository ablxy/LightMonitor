import { useEffect, useState } from "react";
import { fetchTasks, type TaskStatus } from "../api/client";
import TaskCard from "../components/TaskCard";

export default function TaskList() {
  const [tasks, setTasks] = useState<TaskStatus[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchTasks();
        if (!cancelled) setTasks(data);
      } catch (err) {
        if (!cancelled) setError(String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (loading) return <p>Loading tasks…</p>;
  if (error) return <p style={{ color: "#d63031" }}>Error: {error}</p>;
  if (tasks.length === 0) return <p>No configured tasks found.</p>;

  return (
    <>
      <h2 style={{ marginBottom: 16 }}>Detection Tasks</h2>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
          gap: 16,
        }}
      >
        {tasks.map((t) => (
          <TaskCard key={t.stream_id} task={t} />
        ))}
      </div>
    </>
  );
}
