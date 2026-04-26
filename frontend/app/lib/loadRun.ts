import fs from 'node:fs';
import path from 'node:path';
import type { RunData } from './runShape';

const DATA_DIR = path.join(process.cwd(), 'data', 'runs');

export function getRunIds(): string[] {
  // Stable order — landing's §01 cards expect A-001 → A-002 → A-003.
  return ['A-001-patterson', 'A-002-mm-machine', 'A-003-birch-valley'];
}

export function loadRun(id: string): RunData {
  if (!getRunIds().includes(id)) {
    throw new Error(`unknown run id: ${id}`);
  }
  const file = path.join(DATA_DIR, `${id}.json`);
  const raw = fs.readFileSync(file, 'utf-8');
  return JSON.parse(raw) as RunData;
}
