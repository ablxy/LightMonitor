import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import TaskList from "./pages/TaskList";
import TaskDetail from "./pages/TaskDetail";

const headerStyle: React.CSSProperties = {
  background: "#0984e3",
  color: "#fff",
  padding: "12px 24px",
  display: "flex",
  alignItems: "center",
  gap: "16px",
};

export default function App() {
  return (
    <BrowserRouter>
      <header style={headerStyle}>
        <Link to="/" style={{ color: "#fff", textDecoration: "none", fontSize: "1.25rem", fontWeight: 700 }}>
          🎥 LightMonitor
        </Link>
        <span style={{ fontSize: "0.85rem", opacity: 0.8 }}>Video Stream AI Detection Platform</span>
      </header>
      <main style={{ maxWidth: 1200, margin: "24px auto", padding: "0 16px" }}>
        <Routes>
          <Route path="/" element={<TaskList />} />
          <Route path="/tasks/:taskId" element={<TaskDetail />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
