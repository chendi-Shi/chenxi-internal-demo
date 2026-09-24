import { Buffer } from 'node:buffer';

export interface PayloadMetric {
  label: string;
  characters: number;
  utf8_bytes: number;
  ascii_characters: number;
  han_characters: number;
  other_non_ascii_characters: number;
  estimated_tokens: number;
}

export interface TokenEstimationMethod {
  id: 'heuristic-v1';
  formula: string;
  caveat: string;
}

export const tokenEstimationMethod: TokenEstimationMethod = {
  id: 'heuristic-v1',
  formula: 'ceil(ascii_characters / 4 + han_characters + other_non_ascii_characters)',
  caveat: 'Transparent payload estimate only; it is not a model-specific billing tokenizer.',
};

export function compactJson(value: unknown): string {
  return JSON.stringify(value);
}

export function makeMcpEnvelope(payload: unknown): unknown {
  return {
    structuredContent: payload,
    content: [{ type: 'text', text: compactJson(payload) }],
  };
}

export function measureText(label: string, text: string): PayloadMetric {
  let asciiCharacters = 0;
  let hanCharacters = 0;
  let otherNonAsciiCharacters = 0;

  for (const character of text) {
    const codePoint = character.codePointAt(0) ?? 0;
    if (codePoint <= 0x7f) asciiCharacters += 1;
    else if (/\p{Script=Han}/u.test(character)) hanCharacters += 1;
    else otherNonAsciiCharacters += 1;
  }

  return {
    label,
    characters: [...text].length,
    utf8_bytes: Buffer.byteLength(text, 'utf8'),
    ascii_characters: asciiCharacters,
    han_characters: hanCharacters,
    other_non_ascii_characters: otherNonAsciiCharacters,
    estimated_tokens: Math.ceil(
      asciiCharacters / 4 + hanCharacters + otherNonAsciiCharacters,
    ),
  };
}

export function measureJson(label: string, value: unknown): PayloadMetric {
  return measureText(label, compactJson(value));
}

export function sumMetrics(label: string, metrics: PayloadMetric[]): PayloadMetric {
  return {
    label,
    characters: metrics.reduce((sum, metric) => sum + metric.characters, 0),
    utf8_bytes: metrics.reduce((sum, metric) => sum + metric.utf8_bytes, 0),
    ascii_characters: metrics.reduce((sum, metric) => sum + metric.ascii_characters, 0),
    han_characters: metrics.reduce((sum, metric) => sum + metric.han_characters, 0),
    other_non_ascii_characters: metrics.reduce(
      (sum, metric) => sum + metric.other_non_ascii_characters,
      0,
    ),
    estimated_tokens: metrics.reduce((sum, metric) => sum + metric.estimated_tokens, 0),
  };
}
