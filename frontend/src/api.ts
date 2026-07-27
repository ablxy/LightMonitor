export type TaskStatus = {
  stream_id: string;
  stream_name: string;
  status: string;
  labels: string[];
  latest_frame_ts: number | null;
};

export type Detection = {
  label: string;
  confidence: number | null;
};

export type FrameResult = {
  stream_id: string;
  stream_name: string;
  timestamp_ms: number;
  detections: Detection[];
  alarmed: boolean;
  image_base64: string | null;
};

export type HistoryRecord = {
  task_id: string;
  timestamp_ms: number;
  stream_id: string;
  stream_name: string;
  detections: Detection[];
  image_url: string;
};

export type Credentials = { username: string; password: string };

const credentialsKey = 'lightmonitor.credentials';

export function getCredentials(): Credentials | null {
  const raw = sessionStorage.getItem(credentialsKey);
  return raw ? JSON.parse(raw) as Credentials : null;
}

export function saveCredentials(credentials: Credentials): void {
  sessionStorage.setItem(credentialsKey, JSON.stringify(credentials));
}

export function clearCredentials(): void {
  sessionStorage.removeItem(credentialsKey);
}

function authHeader(): HeadersInit {
  const credentials = getCredentials();
  if (!credentials) return {};
  return { Authorization: `Basic ${btoa(`${credentials.username}:${credentials.password}`)}` };
}

async function request<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: authHeader() });
  if (!response.ok) {
    const message = response.status === 401 || response.status === 403
      ? '认证失败，请检查用户名和密码。'
      : `请求失败（${response.status}）`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>('/health'),
  tasks: () => request<TaskStatus[]>('/api/v1/tasks'),
  results: (taskId: string) => request<FrameResult[]>(`/api/v1/tasks/${encodeURIComponent(taskId)}/results?limit=20`),
  history: (params: { streamId?: string; start?: number; end?: number }) => {
    const query = new URLSearchParams({ limit: '100' });
    if (params.streamId) query.set('stream_id', params.streamId);
    if (params.start) query.set('start_ms', String(params.start));
    if (params.end) query.set('end_ms', String(params.end));
    return request<HistoryRecord[]>(`/api/v1/history?${query}`);
  },
};

export function imageSource(image: string | null | undefined): string | undefined {
  if (!image) return undefined;
  return image.startsWith('data:') || image.startsWith('http') ? image : `data:image/jpeg;base64,${image}`;
}
