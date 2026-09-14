export type TaskStatus = {
  stream_id: string;
  stream_name: string;
  status: string;
  labels: string[];
  latest_frame_ts: number | null;
  last_error_code: string | null;
  last_error_message: string | null;
  last_error_at: number | null;
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
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

type ErrorEnvelope = {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
    details?: unknown;
  };
  detail?: string;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId?: string,
    readonly details?: unknown,
  ) {
    super(requestId ? `${message}（请求 ID：${requestId}）` : message);
    this.name = 'ApiError';
  }
}

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
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { headers: authHeader() });
  } catch (reason) {
    throw new ApiError(
      reason instanceof Error ? `无法连接到服务：${reason.message}` : '无法连接到服务。',
      0,
      'NETWORK_ERROR',
    );
  }
  if (!response.ok) {
    let body: ErrorEnvelope = {};
    try {
      body = await response.json() as ErrorEnvelope;
    } catch {
      // Non-JSON proxy and gateway responses use the fallback below.
    }
    const requestId = body.error?.request_id ?? response.headers.get('X-Request-ID') ?? undefined;
    const fallback = response.status === 401 || response.status === 403
      ? '认证失败，请检查用户名和密码。'
      : `请求失败（${response.status}）`;
    throw new ApiError(
      body.error?.message ?? body.detail ?? fallback,
      response.status,
      body.error?.code ?? `HTTP_${response.status}`,
      requestId,
      body.error?.details,
    );
  }
  try {
    return await response.json() as T;
  } catch {
    throw new ApiError('服务返回了无效的数据格式。', response.status, 'INVALID_RESPONSE');
  }
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
