"""Build fresh API, Worker and Web images and run disposable source validation."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
INFRASTRUCTURE = ('postgres:17-alpine', 'rustfs/rustfs:1.0.4')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker-context', default='desktop-linux')
    args = parser.parse_args()
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        parser.error('npm is required')
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(('DOCKER_', 'COMPOSE_', 'NPM_CONFIG_'))
        and key.upper() not in ('NODE_OPTIONS', 'NODE_AUTH_TOKEN', 'NPM_TOKEN')
    }
    docker = ['docker', '--context', args.docker_context]

    def run(command, *, cwd=ROOT):
        subprocess.run(command, cwd=cwd, env=environment, check=True)

    unique = uuid4().hex[:12]
    images = {service: f'vsa-source-{service}:{unique}' for service in ('api', 'worker', 'web')}
    print('Source build tags: ' + json.dumps(images), flush=True)
    for image in INFRASTRUCTURE:
        if subprocess.run(docker + ['image', 'inspect', image], env=environment,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            run(docker + ['pull', image])

    with tempfile.TemporaryDirectory(prefix='vsa-source-build-') as directory:
        workspace = Path(directory)
        frontend = workspace / 'frontend'
        frontend.mkdir()
        for name in ('index.html', 'package.json', 'package-lock.json', 'tsconfig.json', 'vite.config.ts'):
            shutil.copyfile(ROOT / 'frontend' / name, frontend / name)
        shutil.copytree(ROOT / 'frontend' / 'src', frontend / 'src')
        user_config, global_config = workspace / 'npm-user.conf', workspace / 'npm-global.conf'
        user_config.touch()
        global_config.touch()
        run([npm, 'ci', '--userconfig', str(user_config), '--globalconfig', str(global_config),
             '--cache', str(workspace / 'npm-cache'), '--registry', 'https://registry.npmjs.org',
             '--no-audit', '--no-fund'], cwd=frontend)
        run([npm, 'run', 'build'], cwd=frontend)

        # The Web context allowlist receives only newly built assets and the proxy.
        deploy = workspace / 'deploy'
        deploy.mkdir()
        for name in ('Dockerfile.web', 'wp07_web.py', 'Dockerfile.source-worker'):
            shutil.copyfile(ROOT / 'deploy' / name, deploy / name)
        shutil.copyfile(ROOT / '.dockerignore', workspace / '.dockerignore')

        run(docker + ['build', '--no-cache', '--target', 'runtime', '-t', images['api'], 'backend'])
        run(docker + ['build', '--no-cache', '-t', images['worker'], '-f',
                      'Dockerfile.source-worker', '--build-arg', 'VSA_API_BASE=' + images['api'], '.'],
            cwd=deploy)
        run(docker + ['build', '--no-cache', '-t', images['web'], '-f', 'deploy/Dockerfile.web', '.'],
            cwd=workspace)

    command = [
        sys.executable, str(ROOT / 'backend' / 'tools' / 'verify_s3_12.py'),
        '--docker-context', args.docker_context,
        '--api-image', images['api'], '--worker-image', images['worker'], '--web-image', images['web'],
    ]
    run(command)
    run(command + ['--cloud'])


if __name__ == '__main__':
    main()
