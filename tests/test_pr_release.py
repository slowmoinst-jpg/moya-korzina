"""Release selection and the switch between two signed APKs."""
import importlib.util
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("after_pr", Path(__file__).resolve().parents[1] / "tools/release/after-pr.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_selects_merged_main_commit_over_direct_push(monkeypatch):
    def git(*args):
        return "direct\nmerged\nold" if args[0] == "rev-list" else ""

    def github(url):
        sha = url.split("/")[-2]
        return [{"base": {"ref": "main"}, "merged_at": "now" if sha == "merged" else None, "merge_commit_sha": sha}]

    monkeypatch.setattr(release, "git", git)
    monkeypatch.setattr(release, "fetch_json", github)
    assert release.merged_since("last") == "merged"


@pytest.mark.skipif(os.name == "nt", reason="production uses Linux atomic symlinks")
def test_switch_failure_restores_published_apk_and_image(tmp_path, monkeypatch):
    apks = tmp_path / "apk"
    src = tmp_path / "source"
    apks.mkdir()
    (src / "tools").mkdir(parents=True)
    (src / "tools/server-deploy.sh").touch()
    (src / "signed.apk").write_bytes(b"new")
    (apks / "moya-korzina.apk").write_bytes(b"previous")
    (apks / "version.txt").write_text("1.79")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release, "PENDING", tmp_path / "switch.json")
    monkeypatch.setattr(release, "git", lambda *args: "previous-commit")
    monkeypatch.setattr(release, "output", lambda *args: "previous-image")
    monkeypatch.setattr(release, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(release, "idle", lambda: True)
    images = []
    monkeypatch.setattr(release, "start", images.append)

    def fail(*args):
        assert (apks / "version.txt").read_text().strip() == "1.80"
        assert (apks / "moya-korzina.apk").read_bytes() == b"new"
        raise RuntimeError("download unavailable")

    monkeypatch.setattr(release, "verify_download", fail)
    with pytest.raises(RuntimeError, match="download unavailable"):
        release.publish(src, "merged-commit", apks, "https://example.com")
    assert (apks / "version.txt").read_text() == "1.79"
    assert (apks / "moya-korzina.apk").read_bytes() == b"previous"
    assert images == ["korzina:pr-candidate", "previous-image"]
    assert not release.PENDING.exists()
