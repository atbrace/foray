from click.testing import CliRunner

from foray.cli import main


def test_help():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Foray" in result.output


def test_run_help():
    result = CliRunner().invoke(main, ["run", "--help"])
    assert result.exit_code == 0
    assert "--vision" in result.output
    assert "--hours" in result.output
    assert "--max-experiments" in result.output


def test_report_no_run():
    result = CliRunner().invoke(main, ["report"], catch_exceptions=False)
    assert result.exit_code != 0


def test_status_no_run():
    result = CliRunner().invoke(main, ["status"], catch_exceptions=False)
    assert result.exit_code != 0


def test_resume_no_run():
    result = CliRunner().invoke(main, ["resume"], catch_exceptions=False)
    assert result.exit_code != 0
