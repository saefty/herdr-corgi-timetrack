// Pure helpers of the chip, so node can test them without Herdr.

// Is the sampler running? It rewrites status.json on every sample (`at`, seconds); with the default 30 s between
// samples a status younger than 90 s means it is alive. Longer sample times just fall back to track.py --facts.
export const samplerAlive = (status, nowMs) => Number.isFinite(status?.at) && nowMs / 1000 - status.at < 90;
