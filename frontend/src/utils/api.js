export const API_BASE = `${window.location.protocol}//${window.location.hostname}:7860`;

export async function fetchWithAuth(endpoint, options = {}) {
  const token = localStorage.getItem('aura_token');
  
  const headers = {
    ...options.headers,
  };
  
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  
  const res = await fetch(`${API_BASE}${endpoint}`, {
    ...options,
    headers
  });
  
  if (res.status === 401) {
    localStorage.removeItem('aura_token');
    window.dispatchEvent(new Event('logout'));
    throw new Error("Unauthorized");
  }
  
  return res;
}
