from harness.policy import inspect_allowed, path_allowed, unit_allowed
from pathlib import Path


def test_inspect_allows_journalctl_and_ss() -> None:
    assert inspect_allowed(["journalctl", "-n", "20", "--no-pager", "-p", "err"]) is None
    assert inspect_allowed(["ss", "-lntup"]) is None
    assert inspect_allowed(["systemctl", "status", "sshd.service"]) is None
    assert inspect_allowed(["zypper", "--non-interactive", "patch-check"]) is None


def test_inspect_denies_shell_and_destructive() -> None:
    assert inspect_allowed(["journalctl", "-n", "20", "|", "rm"]) is not None
    assert inspect_allowed(["rm", "-rf", "/"]) is not None
    assert inspect_allowed(["bash", "-c", "id"]) is not None
    assert inspect_allowed(["journalctl", "--grep", "secret"]) is not None


def test_inspect_output_flag_allowed() -> None:
    assert inspect_allowed(["journalctl", "-o", "short-iso", "--no-pager"]) is None


def test_path_policy(tmp_path: Path) -> None:
    allowed = (str(tmp_path / "etc-infra"),)
    (tmp_path / "etc-infra").mkdir()
    good = tmp_path / "etc-infra" / "app.conf"
    good.write_text("x")
    assert path_allowed(good, allowed) is None
    assert path_allowed(Path("/etc/shadow"), allowed) is not None
    assert path_allowed(Path("/etc/sudoers"), allowed) is not None
    assert path_allowed(Path("/home/user/.bashrc"), allowed) is not None


def test_unit_allowlist() -> None:
    units = ("sshd.service", "cron.service")
    assert unit_allowed("sshd.service", units) is None
    assert unit_allowed("evil.service", units) is not None
    assert unit_allowed("sshd.service;reboot", units) is not None
