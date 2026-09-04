import subprocess

import pytest

from pkb.opencli_gateway import OpenCliError, OpenCliGateway


class Runner:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, command, **options):
        self.calls.append((command, options))
        if self.error:
            raise self.error
        return self.result


def test_gateway_decodes_json_with_safe_subprocess_options():
    runner = Runner(subprocess.CompletedProcess([], 0, '{"logged_in":true}', ""))
    gateway = OpenCliGateway(runner=runner, executable="opencli.cmd")

    assert gateway.run_json(["zhihu", "whoami", "-f", "json"]) == {"logged_in": True}
    command, options = runner.calls[0]
    assert command == ["opencli.cmd", "zhihu", "whoami", "-f", "json"]
    assert options["encoding"] == "utf-8"
    assert options["errors"] == "replace"
    assert options["timeout"] == 60


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (FileNotFoundError(), "opencli_unavailable"),
        (subprocess.TimeoutExpired([], 60), "opencli_timeout"),
        (subprocess.CalledProcessError(1, [], stderr="private response"), "opencli_failed"),
    ],
)
def test_gateway_maps_process_failures_to_safe_codes(error, code):
    with pytest.raises(OpenCliError, match=f"^{code}$"):
        OpenCliGateway(runner=Runner(error=error), executable="opencli").run_json(["x"])


@pytest.mark.parametrize("stdout", ["not json", "null", "42", '"text"'])
def test_gateway_rejects_malformed_or_non_container_json(stdout):
    runner = Runner(subprocess.CompletedProcess([], 0, stdout, ""))
    with pytest.raises(OpenCliError, match="^opencli_invalid_json$"):
        OpenCliGateway(runner=runner, executable="opencli").run_json(["x"])
