// Free OpenRouter models tried in order when Sarvam is unavailable. OpenRouter retires free
// models often (the previous list was entirely gone), so this can be overridden without a code
// change: NEXT_PUBLIC_OPENROUTER_MODELS="model/a:free,model/b:free".
// Current free models: https://openrouter.ai/models?max_price=0
const DEFAULT_MODELS = [
    "nvidia/nemotron-3.5-lightning:free",
    "inclusionai/ling-3.2-flash:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
];

const configured = (process.env.NEXT_PUBLIC_OPENROUTER_MODELS || "")
    .split(",")
    .map((m) => m.trim())
    .filter(Boolean);

export const OPENROUTER_FREE_MODELS: string[] = configured.length ? configured : DEFAULT_MODELS;
