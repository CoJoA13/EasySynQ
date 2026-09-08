// Deployment policy, distinct from dependency grouping in renovate.json.
module.exports = {
  binarySource: 'global',
  hostRules: ['github.com', 'githubusercontent.com', 'github.io', 'githubassets.com', 'ghcr.io']
    .map(matchHost => ({ matchHost, enabled: false })),
  customEnvVariables: {
    UV_PYTHON: process.env.UV_PYTHON || '3.12',
    UV_PYTHON_DOWNLOADS: 'never',
    UV_ASTRAL_MIRROR_URL: 'https://releases.astral.sh',
  },
};
