const BASE = "/api/v1";

export interface TaskStatus {
  stream_id: string;
  stream_name: string;
  status: string;
  labels: string[];
  latest_frame_ts: number | null;
}

export interface BoundingBox {
  x_min: number;
  y_min: number;
  x_max: number;
  y_max: number;
}

export interface DetectionResult {
  label: string;
  confidence: number;
  bbox: BoundingBox | null;
}

export interface FrameResult {
  stream_id: string;
  stream_name: string;
  timestamp_ms: number;
  detections: DetectionResult[];
  alarmed: boolean;
  image_base64: string | null;
}

export interface TaskDetail {
  task: TaskStatus;
  recent_results: FrameResult[];
}

export async function fetchTasks(): Promise<TaskStatus[]> {
  const resp = await fetch(`${BASE}/tasks`);
  if (!resp.ok) throw new Error(`Failed to fetch tasks: ${resp.status}`);
  return resp.json();
}

export async function fetchTaskDetail(taskId: string): Promise<TaskDetail> {
  const resp = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}`);
  if (!resp.ok) throw new Error(`Failed to fetch task: ${resp.status}`);
  return resp.json();
}

export async function fetchResults(taskId: string, limit = 20): Promise<FrameResult[]> {
  const resp = await fetch(`${BASE}/tasks/${encodeURIComponent(taskId)}/results?limit=${limit}`);
  if (!resp.ok) throw new Error(`Failed to fetch results: ${resp.status}`);
  return resp.json();
}
