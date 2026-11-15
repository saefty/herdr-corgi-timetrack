// Pure helpers of the chip, so node can test them without Herdr.

// Is the sampler running? It rewrites status.json on every sample (`at`, seconds); with the default 30 s between
// samples a status younger than 90 s means it is alive. Longer sample times just fall back to track.py --facts.
export const samplerAlive = (status, nowMs) => Number.isFinite(status?.at) && nowMs / 1000 - status.at < 90;

// The Herdr window's title while a project is being tracked: "<project> - Herdr". track.py puts the project in
// status.title, only with the setting window_title. null (Herdr's own title) when nothing is tracked, the setting is
// off, or the status is older than 3 min (the tracker isn't running).
export const windowTitle = (status, nowMs) =>
  status?.state === "tracking" && typeof status.title === "string" && status.title !== "" && nowMs / 1000 - status.at < 180
    ? `${status.title} - Herdr` : null;
