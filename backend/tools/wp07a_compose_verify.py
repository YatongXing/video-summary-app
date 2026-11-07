"""Exercise the actual WP07-A Compose recipe with disposable synthetic resources."""

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/compose.wp07.yaml"
PACKAGE = "CR-WP07-A"
PREFIX = "vsa-wp07a-test-"
ACCOUNTS = [
    {"email": "user@wp07a.invalid", "role": "USER"},
    {"email": "admin@wp07a.invalid", "role": "ADMIN"},
]
SYNTHETIC_PASSWORD = "WP07A-synthetic-browser-only!"
OPENER = build_opener(ProxyHandler({}))


def docker(*args, timeout=180):
    proc = subprocess.run(
        ["docker", *map(str, args)], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if proc.returncode:
        raise RuntimeError(f"Docker command {args[0]} failed (exit {proc.returncode})")
    return proc.stdout.strip()


def compose(state, *args):
    return docker(
        "compose", "-p", state["id"], "--env-file", state["env_file"],
        "-f", COMPOSE, *args, timeout=360,
    )


def label_filters(project):
    return [
        "--filter", f"label=com.docker.compose.project={project}",
        "--filter", f"label=vsa.work_package={PACKAGE}",
        "--filter", "label=vsa.synthetic_only=true",
    ]


def inventory(state):
    filters = label_filters(state["id"])
    return {
        "containers": docker("ps", "-aq", *filters).splitlines(),
        "volumes": docker("volume", "ls", "-q", *filters).splitlines(),
        "networks": docker("network", "ls", "-q", *filters).splitlines(),
    }


def validate_owned(state):
    project = state["id"]
    assert project.startswith(PREFIX)
    resources = inventory(state)
    for kind, names in resources.items():
        inspect_kind = {"containers": "container", "volumes": "volume", "networks": "network"}[kind]
        for name in names:
            obj = json.loads(docker(inspect_kind, "inspect", name))[0]
            label = obj["Config"]["Labels"] if kind == "containers" else obj["Labels"]
            assert label["com.docker.compose.project"] == project
            assert label["vsa.work_package"] == PACKAGE and label["vsa.synthetic_only"] == "true"
            assert obj["Name"].lstrip("/").startswith(project)
            if kind == "containers":
                for mount in obj["Mounts"]:
                    assert mount["Type"] == "volume" and mount["Name"] in resources["volumes"]
    project_only = ["--filter", f"label=com.docker.compose.project={project}"]
    assert set(docker("ps", "-aq", *project_only).splitlines()) == set(resources["containers"])
    assert set(docker("volume", "ls", "-q", *project_only).splitlines()) == set(resources["volumes"])
    assert set(docker("network", "ls", "-q", *project_only).splitlines()) == set(resources["networks"])
    return resources


def status(base, path):
    try:
        with OPENER.open(base + path, timeout=4) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except URLError:
        return None


def wait(fn, seconds=90):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(.3)
    raise AssertionError("Dedicated Compose condition timed out")


def new_state():
    project = PREFIX + "compose-" + uuid4().hex[:8]
    assert not any(project in docker(*args) for args in (
        ("ps", "-a", "--format", "{{.Names}}"),
        ("volume", "ls", "-q"), ("network", "ls", "--format", "{{.Name}}"),
    ))
    root = Path(tempfile.mkdtemp(prefix="video-summary-wp07a-compose-"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = root / "synthetic.env"
    values = {
        "WP07_PROJECT_NAME": project,
        "WP07_SYNTHETIC_ONLY": "true",
        "WP07_HTTP_PORT": port,
        "WP07_DB": "wp07a_compose_test",
        "WP07_INIT_PASSWORD": secrets.token_hex(16),
        "WP07_MAINT_PASSWORD": secrets.token_hex(16),
        "WP07_RUNTIME_PASSWORD": secrets.token_hex(16),
        "WP07_OBJECT_ACCESS_KEY": "wp07a" + secrets.token_hex(5),
        "WP07_OBJECT_SECRET_KEY": secrets.token_hex(16),
        "WP07_APP_IMAGE": "video-summary-app-wp07a-compose-test:latest",
        "WP07_WEB_IMAGE": "video-summary-app-wp07a-web-test:latest",
        "SUPPORT_EMAIL": "feedback@example.invalid",
    }
    env.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")
    return {
        "id": project, "package": PACKAGE, "synthetic_only": True,
        "root": str(root), "env_file": str(env), "state": str(root / "state.json"),
        "url": f"http://127.0.0.1:{port}", "api_url": f"http://127.0.0.1:{port}",
        "api_path_prefix": "/api",
        "accounts": [{**account, "password": SYNTHETIC_PASSWORD} for account in ACCOUNTS],
        "containers": {
            "api": project + "-api-1", "db": project + "-db-1",
            "s3": project + "-object-store-1", "web": project + "-web-1",
            "workers": [project + "-worker-1", project + "-worker-2"],
        },
    }


def maintenance(state, command):
    compose(state, "run", "--rm", "--no-deps", "maintenance", "python", "-m", "app.maintenance", command)


def start(state):
    root = Path(state["root"])
    Path(state["state"]).write_text(json.dumps(state), encoding="utf-8")
    print(json.dumps({"state": state["state"], "project": state["id"]}), flush=True)
    compose(state, "build", "api", "web")
    compose(state, "up", "-d", "--wait", "--wait-timeout", "90", "db", "object-store")
    validate_owned(state)
    compose(state, "run", "--rm", "--no-deps", "volume-init")
    compose(state, "run", "--rm", "--no-deps", "initialize")
    try:
        compose(state, "run", "--rm", "--no-deps", "initialize")
    except RuntimeError:
        print("A01 initialized database rejects repeat initialization", flush=True)
    else:
        raise AssertionError("Repeat initialization unexpectedly succeeded")
    compose(state, "up", "-d", "--scale", "worker=2", "api", "worker", "web")
    resources = validate_owned(state)
    assert len(resources["containers"]) == 6 and len(resources["volumes"]) == 5
    print("A01-A04 inspected six scoped containers and five private volumes", flush=True)
    for kind in ("db", "s3", "api"):
        name = state["containers"][kind]
        published = json.loads(docker("inspect", "--format", "{{json .HostConfig.PortBindings}}", name))
        assert not published, kind
    web_ports = json.loads(docker("inspect", "--format", "{{json .HostConfig.PortBindings}}", state["containers"]["web"]))
    assert web_ports == {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": state["url"].rsplit(":", 1)[1]}]}
    for name in [state["containers"]["api"], state["containers"]["web"], *state["containers"]["workers"]]:
        assert docker("exec", name, "id", "-u") == "10001"
    base = state["url"] + "/api"
    wait(lambda: status(base, "/health/live") == 200)
    assert status(base, "/health/ready") == 503
    assert status(base, "/tasks") == 503
    seed = (
        "import os; os.environ['DATABASE_URL']=os.environ['MAINTENANCE_DATABASE_URL']; "
        "from app import auth; c=auth.connect(); "
        f"rows={ACCOUNTS!r}; "
        f"[(c.execute('INSERT INTO users(email_normalized,password_hash,role) VALUES(%s,%s,%s)', "
        f"(row['email'], auth.password_hash({SYNTHETIC_PASSWORD!r}), row['role']))) for row in rows]; "
        "c.commit(); c.close()"
    )
    compose(state, "run", "--rm", "--no-deps", "maintenance", "python", "-c", seed)
    out = "/control/processing/wp07a-browser-synthetic.mp4"
    docker(
        "exec", state["containers"]["api"], "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=12:duration=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
        "-shortest", out,
    )
    sample = root / "long.mp4"
    docker("cp", state["containers"]["api"] + ":" + out, sample)
    state["samples"] = {"long": str(sample)}
    Path(state["state"]).write_text(json.dumps(state), encoding="utf-8")
    compose(state, "restart", "api", "worker", "web")
    wait(lambda: status(base, "/health/live") == 200)
    assert status(base, "/health/ready") == 503 and status(base, "/tasks") == 503
    print("A05-A07 Compose CLOSED retained through API/Worker/web restart", flush=True)
    maintenance(state, "open")
    wait(lambda: status(base, "/health/ready") == 200)
    assert status(base, "/tasks") in (401, 403)
    print("Compose OPEN established by maintenance identity", flush=True)


def browser(state):
    node = shutil.which("node")
    if not node:
        node = next((str(path) for path in Path(r"C:\Users").glob("*/AppData/Local/Programs/nodejs/node.exe")), None)
    assert node, "Node runtime required for browser verification"
    env = {**os.environ, "WP06_STATE": state["state"]}
    proc = subprocess.run(
        [node, str(ROOT / "frontend/tests/wp07a-real.mjs")],
        cwd=ROOT, env=env, timeout=300,
    )
    assert proc.returncode == 0, "Actual browser verification failed"


def stop(state):
    validate_owned(state)
    compose(state, "down", "--volumes", "--remove-orphans")
    assert not any(inventory(state).values())
    root = Path(state["root"]).resolve()
    assert root.parent == Path(tempfile.gettempdir()).resolve()
    assert root.name.startswith("video-summary-wp07a-compose-")
    shutil.rmtree(root)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["run", "start", "stop"])
    parser.add_argument("--state")
    args = parser.parse_args()
    if args.action == "stop":
        state = json.loads(Path(args.state).read_text(encoding="utf-8"))
        assert state["package"] == PACKAGE and state["synthetic_only"] is True
        stop(state)
        return
    state = new_state()
    try:
        start(state)
        if args.action == "run":
            browser(state)
    finally:
        if args.action == "run":
            stop(state)


if __name__ == "__main__":
    main()
