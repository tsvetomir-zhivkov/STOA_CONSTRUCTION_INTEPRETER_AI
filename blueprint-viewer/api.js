const unavailable = 'The drawing service is unavailable. Stop the static preview server, then run npm start from the repository root and open the workspace from that server.';

export async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, options); }
  catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Cannot reach the drawing service. Check that npm start is still running and try again.');
  }
  if (!(response.headers.get('content-type') ?? '').includes('application/json')) {
    throw new Error(unavailable);
  }
  const value = await response.json();
  if (!response.ok) throw new Error(value.error ?? response.statusText);
  return value;
}
