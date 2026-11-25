"""Publish merged main commits on the production host. No GitHub write token is needed."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path("/opt/korzina/pr-release")
PROGRAMS = Path(__file__).resolve().parent
REPO = ROOT / "repo.git"
APPLIED = "refs/korzina/applied"
API = "https://api.github.com/repos/slowmoinst-jpg/moya-korzina"
PENDING = ROOT / "switch.json"


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def git(*args):
    return output("git", "--git-dir", REPO, *args)


def fetch_json(url, **headers):
    with urlopen(Request(url, headers={"User-Agent": "korzina-release", **headers}), timeout=25) as response:
        return json.load(response)


def merged_since(last):
    git("fetch", "--quiet", "--no-tags", "origin", "refs/heads/main:refs/heads/main")
    git("merge-base", "--is-ancestor", last, "refs/heads/main")
    # Use the newest merged PR. A plain push, an open PR and a closed unmerged PR
    # must never promote a change. Recheck recent direct pushes for API propagation.
    for sha in git("rev-list", "--first-parent", last + "..refs/heads/main").splitlines():
        prs = fetch_json(f"{API}/commits/{sha}/pulls")
        if any(p["merged_at"] and p["base"]["ref"] == "main" and p["merge_commit_sha"] == sha for p in prs):
            return sha
    return None


def atomic_text(path, text):
    temp = path.with_suffix(".new")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def point(link, target):
    temp = link.with_name("." + link.name + ".next")
    temp.unlink(missing_ok=True)
    temp.symlink_to(target)
    temp.replace(link)


def archive_previous(apks):
    # Convert the original pair of files in place; both old URLs keep working.
    current = apks / "current"
    if not current.is_symlink() or not all(
        (apks / name).is_symlink() and os.readlink(apks / name) == f"current/{name}"
        for name in ("moya-korzina.apk", "version.txt")
    ):
        saved = Path(tempfile.mkdtemp(prefix="previous-", dir=apks / "releases"))
        for name in ("moya-korzina.apk", "version.txt"):
            shutil.copyfile(apks / name, saved / name)
        saved.chmod(0o755)
        point(current, str(saved.relative_to(apks)))
        for name in ("moya-korzina.apk", "version.txt"):
            point(apks / name, "current/" + name)
    return os.readlink(current)


def start(image):
    run("bash", PROGRAMS / "server-deploy.sh", env={**os.environ, "KORZINA_IMAGE": image})
    run("curl", "-fsS", "-o", "/dev/null", "--retry", "8", "--retry-connrefused",
        "--max-time", "5", "http://127.0.0.1:8765/api/health")


def recover(apks):
    if PENDING.exists():
        previous = json.loads(PENDING.read_text())
        if git("rev-parse", APPLIED) != previous["target"]:
            print("Restoring last published release", flush=True)
            point(apks / "current", previous["apk"])
            start(previous["image"])
            run("docker", "tag", previous["image"], "korzina:latest")
        PENDING.unlink()


def idle():
    code = subprocess.run(["docker", "exec", "korzina-jobs", "python", "-m", "app.catalog.worker", "--runs"]).returncode
    if code == 3:
        print("Catalog crawl in progress; release deferred.", flush=True)
        return False
    if code:
        raise RuntimeError("Could not check the catalog worker")
    return True


def verify_download(origin, previous, version, digest):
    update = fetch_json(origin + "/api/auth/me", **{"User-Agent": "KorzinaApp/" + previous}).get("apkUpdate", {})
    url = origin + "/static/moya-korzina.apk"
    if (update.get("version"), update.get("canDownload"), update.get("url")) != (version, True, url):
        raise RuntimeError("Update metadata did not match the built APK")
    with urlopen(url, timeout=45) as response:
        if response.geturl() != url or hashlib.file_digest(response, "sha256").hexdigest() != digest:
            raise RuntimeError("Downloaded APK did not match the signed build")


def publish(src, sha, apks, origin):
    old = (apks / "version.txt").read_text().strip()
    if not re.fullmatch(r"\d+(\.\d+)+", old):
        raise ValueError("Invalid published version")
    prefix, patch = old.rsplit(".", 1)
    version = f"{prefix}.{int(patch) + 1}"
    run("bash", PROGRAMS / "build.sh", str(src), sha, version, str((apks / "moya-korzina.apk").resolve()))
    if not idle() or (apks / "distribution.paused").exists():
        return False
    releases = apks / "releases"
    releases.mkdir(exist_ok=True)
    previous_apk = archive_previous(apks)
    target = releases / sha
    target.mkdir(exist_ok=True)
    shutil.copyfile(src / "signed.apk", target / "moya-korzina.apk")
    (target / "version.txt").write_text(version + "\n")
    digest = hashlib.sha256((target / "moya-korzina.apk").read_bytes()).hexdigest()
    old_image = output("docker", "inspect", "-f", "{{.Image}}", "korzina")
    run("docker", "tag", old_image, "korzina:pr-previous")
    atomic_text(PENDING, json.dumps(dict(target=sha, apk=previous_apk, image=old_image)))
    try:
        start("korzina:pr-candidate")
        point(apks / "current", "releases/" + sha)
        verify_download(origin, old, version, digest)
        run("docker", "tag", "korzina:pr-candidate", "korzina:latest")
        git("update-ref", APPLIED, sha)
    except BaseException:
        recover(apks)
        raise
    PENDING.unlink()
    print(f"Published {version} ({sha}), APK sha256 {digest}", flush=True)
    return True


def main():
    import fcntl

    origin = os.environ["KORZINA_SERVER"].rstrip("/")
    if not origin.startswith("https://"):
        raise ValueError("Expected HTTPS origin")
    apks = Path(output("docker", "volume", "inspect", "-f", "{{.Mountpoint}}", "korzina-data")) / "apk"
    with open("/opt/korzina/deploy.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        recover(apks)
        last = git("rev-parse", APPLIED)
        sha = merged_since(last)
        if not sha or (apks / "distribution.paused").exists():
            return
        retry = ROOT / "attempt"
        if retry.exists() and retry.read_text() == sha and time.time() - retry.stat().st_mtime < 1800:
            return
        if not idle():
            return
        with tempfile.TemporaryDirectory(prefix="build-", dir=ROOT) as work:
            src = Path(work)
            tar = src / "source.tar"
            git("archive", "--output", str(tar), sha)
            run("tar", "-xf", tar, "-C", src)
            tar.unlink()
            print(f"Building merged main commit {sha}", flush=True)
            atomic_text(retry, sha)
            publish(src, sha, apks, origin)
            retry.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
