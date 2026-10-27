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

// Do not use shell:true — paths with spaces (e.g. "My All Data") get split by cmd.exe.
const child = spawn(
  python,
  ['-m', 'uvicorn', 'api.main:app', '--reload', '--port', '8000'],
  { cwd: root, stdio: 'inherit', windowsHide: true },
);

child.on('exit', (code) => process.exit(code ?? 1));
