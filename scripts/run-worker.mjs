import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const venvPython = path.join(
  root,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
const python = existsSync(venvPython) ? venvPython : 'python';

// Celery's default 'prefork' pool relies on fork() and does not work on Windows —
// tasks are accepted but never execute. 'solo' runs them in-process and is the
// reliable dev default here; override with CELERY_POOL=threads for parallelism.
const pool = process.env.CELERY_POOL ?? (process.platform === 'win32' ? 'solo' : 'prefork');

const args = ['-m', 'celery', '-A', 'worker.celery_app', 'worker', '-l', 'info', `--pool=${pool}`];
// The solo pool is single-task by definition; passing --concurrency there is rejected.
if (pool !== 'solo' && process.env.CELERY_CONCURRENCY) {
  args.push(`--concurrency=${process.env.CELERY_CONCURRENCY}`);
}

// Do not use shell:true — paths with spaces (e.g. "My All Data") get split by cmd.exe.
const child = spawn(python, args, { cwd: root, stdio: 'inherit', windowsHide: true });

child.on('exit', (code) => process.exit(code ?? 1));
