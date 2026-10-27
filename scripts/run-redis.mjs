// Start the Celery broker, whichever Redis this machine actually has.
//
//   1. A local redis-server binary (REDIS_HOME, or the default install path).
//      Used on Windows boxes without Docker — Docker Desktop needs WSL2 +
//      hardware virtualization + a reboot, which is a lot to ask for a broker.
//   2. Otherwise the docker-compose "redis" service.
//
// Already-running is success, not an error: this script is safe to re-run.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const isWin = process.platform === 'win32';
const exe = (name) => (isWin ? `${name}.exe` : name);

const redisHome = process.env.REDIS_HOME || path.join(os.homedir(), 'redis');
const server = path.join(redisHome, exe('redis-server'));
const cli = path.join(redisHome, exe('redis-cli'));
const conf = path.join(redisHome, 'redis.local.conf');

function pings() {
  if (!existsSync(cli)) return false;
  const out = spawnSync(cli, ['ping'], { encoding: 'utf8', windowsHide: true });
  return out.stdout?.trim() === 'PONG';
}

if (process.argv.includes('--stop')) {
  if (!pings()) {
    console.log('Redis is not running.');
    process.exit(0);
  }
  // SHUTDOWN closes the connection without a reply, so redis-cli exits non-zero
  // on success — trust the follow-up PING instead of the exit code.
  spawnSync(cli, ['shutdown', 'nosave'], { windowsHide: true });
  console.log(pings() ? 'Redis still responding — shutdown refused.' : 'Redis stopped.');
  process.exit(pings() ? 1 : 0);
}

if (pings()) {
  console.log('Redis already running on 6379.');
  process.exit(0);
}

if (existsSync(server)) {
  const args = existsSync(conf) ? [conf] : [];
  // detached + unref so the broker outlives this npm script, like `docker compose -d`.
  const child = spawn(server, args, {
    cwd: redisHome,
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
  });
  child.unref();
  // redis-server takes a moment to bind; poll rather than guess at a sleep.
  const deadline = Date.now() + 10_000;
  const wait = () => {
    if (pings()) {
      console.log(`Redis started from ${redisHome} on 6379.`);
      process.exit(0);
    }
    if (Date.now() > deadline) {
      console.error(`redis-server did not answer PING within 10s. See ${path.join(redisHome, 'redis.log')}`);
      process.exit(1);
    }
    setTimeout(wait, 250);
  };
  wait();
} else {
  console.log(`No redis-server at ${redisHome} — falling back to docker compose.`);
  const child = spawn('docker', ['compose', 'up', '-d', 'redis'], {
    cwd: path.resolve(import.meta.dirname, '..'),
    stdio: 'inherit',
    windowsHide: true,
  });
  child.on('error', () => {
    console.error(
      'Neither a local redis-server nor Docker is available.\n' +
        'Install one of:\n' +
        `  * portable Redis -> unzip to ${redisHome} (or set REDIS_HOME)\n` +
        '  * Docker Desktop -> then `docker compose up -d redis`',
    );
    process.exit(1);
  });
  child.on('exit', (code) => process.exit(code ?? 1));
}
