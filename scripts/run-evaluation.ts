import { mkdir, readFile, writeFile } from 'node:fs/promises';

import { buildEvaluationReport, renderEvaluationMarkdown } from '../src/evaluation/report.ts';

const jsonPath = 'artifacts/evaluation/latest.json';
const markdownPath = 'docs/evaluation/BASELINE.md';
const report = buildEvaluationReport();
const json = `${JSON.stringify(report, null, 2)}\n`;
const markdown = `${renderEvaluationMarkdown(report)}\n`;

if (process.argv.includes('--check')) {
  const [currentJson, currentMarkdown] = await Promise.all([
    readFile(jsonPath, 'utf8').catch(() => ''),
    readFile(markdownPath, 'utf8').catch(() => ''),
  ]);
  if (currentJson !== json || currentMarkdown !== markdown) {
    throw new Error('Evaluation artifacts are stale; run npm run evaluate');
  }
  console.log(`Evaluation artifacts are current: ${jsonPath}, ${markdownPath}`);
} else {
  await Promise.all([
    mkdir('artifacts/evaluation', { recursive: true }),
    mkdir('docs/evaluation', { recursive: true }),
  ]);
  await Promise.all([
    writeFile(jsonPath, json, 'utf8'),
    writeFile(markdownPath, markdown, 'utf8'),
  ]);
  console.log(
    `Evaluation complete: ${report.experiment_summary.passed} passed, ` +
      `${report.experiment_summary.failed} failed, ${report.experiment_summary.not_run} not run.`,
  );
  console.log(`Wrote ${jsonPath} and ${markdownPath}.`);
}
